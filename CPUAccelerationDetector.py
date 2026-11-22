import sys
import platform
import subprocess
import json
import time
import threading
import logging
import traceback
import os
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeoutError

# 配置日志系统
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('cpu_acceleration_detector.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class CPUAccelerationDetector:
    """CPU加速方案检测器核心类"""
    
    def __init__(self):
        self.supported_features = {}
        self.detection_timeout = 30  # 每个检测项的超时时间(秒)
        self.max_workers = 4  # 最大并发线程数
        self.cache_enabled = True
        self.cache_file = 'cpu_features_cache.json'
        self.cache_expiry = 3600  # 缓存有效期(秒)
        
    def _execute_command(self, command: str, timeout: int = 10) -> Tuple[bool, str]:
        """执行系统命令并返回结果"""
        try:
            result = subprocess.run(
                command,
                shell=True,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout
            )
            return (result.returncode == 0, result.stdout.strip())
        except subprocess.TimeoutExpired:
            logger.warning(f"命令执行超时: {command}")
            return (False, "命令执行超时")
        except Exception as e:
            logger.error(f"执行命令出错: {command}, 错误: {str(e)}")
            return (False, f"命令执行出错: {str(e)}")
    
    def _check_cache_valid(self) -> bool:
        """检查缓存是否有效"""
        if not self.cache_enabled or not os.path.exists(self.cache_file):
            return False
            
        try:
            file_time = os.path.getmtime(self.cache_file)
            current_time = time.time()
            return (current_time - file_time) < self.cache_expiry
        except Exception as e:
            logger.warning(f"检查缓存有效期失败: {str(e)}")
            return False
    
    def _load_from_cache(self) -> Optional[Dict]:
        """从缓存加载数据"""
        if not self._check_cache_valid():
            return None
            
        try:
            with open(self.cache_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict) and 'timestamp' in data:
                    return data
                return None
        except Exception as e:
            logger.warning(f"从缓存加载数据失败: {str(e)}")
            return None
    
    def _save_to_cache(self, data: Dict) -> bool:
        """保存数据到缓存"""
        if not self.cache_enabled:
            return False
            
        try:
            data['timestamp'] = time.time()
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            return True
        except Exception as e:
            logger.warning(f"保存数据到缓存失败: {str(e)}")
            return False
    
    def _detect_cpu_info(self) -> Dict:
        """检测CPU基本信息"""
        cpu_info = {
            'cpu_name': '未知',
            'cpu_cores': 0,
            'cpu_threads': 0,
            'architecture': platform.machine(),
            'system': platform.system(),
            'platform': platform.platform()
        }
        
        try:
            if platform.system() == 'Windows':
                success, output = self._execute_command('wmic cpu get name')
                if success and output:
                    lines = output.split('\n')
                    if len(lines) > 1:
                        cpu_info['cpu_name'] = lines[1].strip()
                
                success, output = self._execute_command('wmic cpu get NumberOfCores')
                if success and output:
                    lines = output.split('\n')
                    if len(lines) > 1 and lines[1].strip().isdigit():  # 添加数字检查
                        cpu_info['cpu_cores'] = int(lines[1].strip())
                
                success, output = self._execute_command('wmic cpu get NumberOfLogicalProcessors')
                if success and output:
                    lines = output.split('\n')
                    if len(lines) > 1 and lines[1].strip().isdigit():  # 添加数字检查
                        cpu_info['cpu_threads'] = int(lines[1].strip())
            
            elif platform.system() == 'Linux':
                success, output = self._execute_command('lscpu')
                if success and output:
                    for line in output.split('\n'):
                        if 'Model name:' in line:
                            cpu_info['cpu_name'] = line.split(':')[1].strip()
                        elif 'CPU(s):' in line and not 'NUMA' in line:
                            value = line.split(':')[1].strip()
                            if value.isdigit():  # 添加数字检查
                                cpu_info['cpu_threads'] = int(value)
                        elif 'Core(s) per socket:' in line:
                            value = line.split(':')[1].strip()
                            if value.isdigit():  # 添加数字检查
                                cores_per_socket = int(value)
                        elif 'Socket(s):' in line:
                            value = line.split(':')[1].strip()
                            if value.isdigit():  # 添加数字检查
                                sockets = int(value)
                                cpu_info['cpu_cores'] = cores_per_socket * sockets
            
            elif platform.system() == 'Darwin':  # macOS
                success, output = self._execute_command('sysctl -n machdep.cpu.brand_string')
                if success and output:
                    cpu_info['cpu_name'] = output.strip()
                
                success, output = self._execute_command('sysctl -n hw.physicalcpu')
                if success and output and output.strip().isdigit():  # 添加数字检查
                    cpu_info['cpu_cores'] = int(output.strip())
                
                success, output = self._execute_command('sysctl -n hw.logicalcpu')
                if success and output and output.strip().isdigit():  # 添加数字检查
                    cpu_info['cpu_threads'] = int(output.strip())
        
        except Exception as e:
            logger.error(f"检测CPU信息时出错: {str(e)}")
        
        return cpu_info

    
    def _detect_sse(self) -> Dict:
        """检测SSE指令集支持"""
        sse_versions = ['sse', 'sse2', 'sse3', 'ssse3', 'sse4.1', 'sse4.2']
        result = {'supported': [], 'details': {}}
        
        try:
            if platform.system() == 'Windows':
                success, output = self._execute_command('wmic cpu get caption /value')
                if success:
                    for version in sse_versions:
                        if version.upper() in output.upper():
                            result['supported'].append(version)
                            result['details'][version] = True
                        else:
                            result['details'][version] = False
            
            elif platform.system() in ['Linux', 'Darwin']:
                success, output = self._execute_command('cat /proc/cpuinfo' if platform.system() == 'Linux' else 'sysctl -a')
                if success:
                    for version in sse_versions:
                        if version.upper() in output.upper():
                            result['supported'].append(version)
                            result['details'][version] = True
                        else:
                            result['details'][version] = False
        
        except Exception as e:
            logger.error(f"检测SSE指令集时出错: {str(e)}")
        
        return result
    
    def _detect_avx(self) -> Dict:
        """检测AVX指令集支持"""
        avx_versions = ['avx', 'avx2', 'avx512']
        result = {'supported': [], 'details': {}}
        
        try:
            if platform.system() == 'Windows':
                success, output = self._execute_command('wmic cpu get caption /value')
                if success:
                    for version in avx_versions:
                        if version.upper() in output.upper():
                            result['supported'].append(version)
                            result['details'][version] = True
                        else:
                            result['details'][version] = False
            
            elif platform.system() in ['Linux', 'Darwin']:
                success, output = self._execute_command('cat /proc/cpuinfo' if platform.system() == 'Linux' else 'sysctl -a')
                if success:
                    for version in avx_versions:
                        if version.upper() in output.upper():
                            result['supported'].append(version)
                            result['details'][version] = True
                        else:
                            result['details'][version] = False
            
            # 额外的AVX-512检测
            if 'avx512' in result['supported']:
                # 检查AVX-512的具体子集
                avx512_subset = {
                    'avx512f': False, 'avx512cd': False, 'avx512bw': False,
                    'avx512dq': False, 'avx512vl': False, 'avx512ifma': False,
                    'avx512vbmi': False
                }
                
                if platform.system() == 'Linux':
                    success, output = self._execute_command('cat /proc/cpuinfo')
                    if success:
                        for subset in avx512_subset.keys():
                            if subset in output.lower():
                                avx512_subset[subset] = True
                
                result['details']['avx512_subset'] = avx512_subset
        
        except Exception as e:
            logger.error(f"检测AVX指令集时出错: {str(e)}")
        
        return result
    
    def _detect_neon(self) -> Dict:
        """检测ARM NEON指令集支持"""
        result = {'supported': False, 'details': {}}
        
        try:
            if platform.machine().lower().startswith(('arm', 'aarch')):
                if platform.system() == 'Linux':
                    success, output = self._execute_command('cat /proc/cpuinfo')
                    if success and 'neon' in output.lower():
                        result['supported'] = True
                        result['details']['neon'] = True
                
                elif platform.system() == 'Darwin':  # Apple Silicon
                    success, output = self._execute_command('sysctl -a')
                    if success and 'neon' in output.lower():
                        result['supported'] = True
                        result['details']['neon'] = True
            
            # 检查是否在ARM平台上
            result['details']['is_arm'] = platform.machine().lower().startswith(('arm', 'aarch'))
        
        except Exception as e:
            logger.error(f"检测NEON指令集时出错: {str(e)}")
        
        return result
    
    def _detect_fma(self) -> Dict:
        """检测FMA指令集支持"""
        result = {'supported': False, 'details': {}}
        
        try:
            if platform.system() == 'Windows':
                success, output = self._execute_command('wmic cpu get caption /value')
                if success and 'FMA' in output.upper():
                    result['supported'] = True
                    result['details']['fma'] = True
            
            elif platform.system() in ['Linux', 'Darwin']:
                success, output = self._execute_command('cat /proc/cpuinfo' if platform.system() == 'Linux' else 'sysctl -a')
                if success and 'fma' in output.lower():
                    result['supported'] = True
                    result['details']['fma'] = True
        
        except Exception as e:
            logger.error(f"检测FMA指令集时出错: {str(e)}")
        
        return result
    
    def _detect_simd(self) -> Dict:
        """检测SIMD支持情况"""
        result = {
            'sse': self._detect_sse(),
            'avx': self._detect_avx(),
            'neon': self._detect_neon(),
            'fma': self._detect_fma()
        }
        return result
    
    def _detect_openmp(self) -> Dict:
        """检测OpenMP支持"""
        result = {'supported': False, 'version': '未知', 'details': {}}
        
        try:
            # 首先检查gcc是否可用
            success, _ = self._execute_command('gcc --version')
            if not success:
                logger.warning("未检测到gcc编译器，跳过OpenMP检测")
                return result

            # 尝试编译并运行一个简单的OpenMP程序
            test_code = """
            #include <omp.h>
            #include <stdio.h>
            int main() {
                #pragma omp parallel
                {
                    printf("OpenMP支持, 线程数: %d\\n", omp_get_num_threads());
                }
                return 0;
            }
            """
            
            with open('openmp_test.c', 'w') as f:
                f.write(test_code)
            
            compile_cmd = 'gcc -fopenmp openmp_test.c -o openmp_test'
            success, _ = self._execute_command(compile_cmd, timeout=60)  # 增加超时时间
            
            if success:
                run_success, output = self._execute_command('./openmp_test' if platform.system() != 'Windows' else 'openmp_test.exe')
                if run_success and 'OpenMP支持' in output:
                    result['supported'] = True
                    # 提取OpenMP版本
                    version_success, version_output = self._execute_command('gcc -dumpversion')
                    if version_success:
                        result['version'] = version_output.strip()
            
            # 清理临时文件
            if os.path.exists('openmp_test.c'):
                os.remove('openmp_test.c')
            if os.path.exists('openmp_test' if platform.system() != 'Windows' else 'openmp_test.exe'):
                os.remove('openmp_test' if platform.system() != 'Windows' else 'openmp_test.exe')
        
        except Exception as e:
            logger.error(f"检测OpenMP支持时出错: {str(e)}")
        
        return result
    
    def _detect_openmpi(self) -> Dict:
        """检测OpenMPI支持"""
        result = {'supported': False, 'version': '未知', 'details': {}}
        
        try:
            success, output = self._execute_command('mpirun --version')
            if success and ('Open MPI' in output or 'MPICH' in output):
                result['supported'] = True
                version_match = re.search(r'(\d+\.\d+\.\d+)', output)
                if version_match:
                    result['version'] = version_match.group(1)
        
        except Exception as e:
            logger.error(f"检测OpenMPI支持时出错: {str(e)}")
        
        return result
    
    def _detect_tbb(self) -> Dict:
        """检测Intel TBB支持"""
        result = {'supported': False, 'version': '未知', 'details': {}}
        
        try:
            # 检查是否安装了TBB
            if platform.system() == 'Windows':
                success, output = self._execute_command('where tbb')
            else:
                success, output = self._execute_command('which tbb')
            
            if success:
                result['supported'] = True
                # 尝试获取版本信息
                version_success, version_output = self._execute_command('tbb --version')
                if version_success:
                    result['version'] = version_output.strip()
        
        except Exception as e:
            logger.error(f"检测Intel TBB支持时出错: {str(e)}")
        
        return result
    
    def _detect_parallel_frameworks(self) -> Dict:
        """检测并行计算框架支持"""
        result = {
            'openmp': self._detect_openmp(),
            'openmpi': self._detect_openmpi(),
            'tbb': self._detect_tbb()
        }
        return result
    
    def _detect_python_accelerations(self) -> Dict:
        """检测Python加速支持"""
        result = {
            'numpy': {'supported': False, 'version': '未知', 'details': {}},
            'numba': {'supported': False, 'version': '未知', 'details': {}},
            'cython': {'supported': False, 'version': '未知', 'details': {}}
        }
        
        try:
            # 检查NumPy
            import numpy
            result['numpy']['supported'] = True
            result['numpy']['version'] = numpy.__version__
            
            # 新的BLAS检测方法
            try:
                import numpy.distutils.system_info as sysinfo
                blas_info = sysinfo.get_info('blas_opt')
                lapack_info = sysinfo.get_info('lapack_opt')
                result['numpy']['details']['blas'] = blas_info if blas_info else '未检测到优化的BLAS'
                result['numpy']['details']['lapack'] = lapack_info if lapack_info else '未检测到优化的LAPACK'
            except Exception as e:
                logger.warning(f"检测NumPy BLAS支持时出错: {str(e)}")
                result['numpy']['details']['blas'] = '检测失败'
                result['numpy']['details']['lapack'] = '检测失败'
            
            # 检查Numba
            try:
                import numba
                result['numba']['supported'] = True
                result['numba']['version'] = numba.__version__
                
                # 检查Numba支持的CPU特性
                try:
                    from numba import __helper__
                    cpu_features = __helper__.cpu_features()
                    result['numba']['details']['cpu_features'] = cpu_features
                except:
                    pass
            except ImportError:
                pass
            
            # 检查Cython
            try:
                import cython
                result['cython']['supported'] = True
                result['cython']['version'] = cython.__version__
            except ImportError:
                pass
        
        except Exception as e:
            logger.error(f"检测Python加速支持时出错: {str(e)}")
        
        return result
    
    def _detect_gpu_acceleration(self) -> Dict:
        """检测GPU加速支持"""
        result = {
            'cuda': {'supported': False, 'version': '未知', 'details': {}},
            'opencl': {'supported': False, 'version': '未知', 'details': {}}
        }
        
        try:
            # 检测CUDA
            if platform.system() == 'Windows':
                success, output = self._execute_command('nvcc --version')
                if success:
                    result['cuda']['supported'] = True
                    version_match = re.search(r'release (\d+\.\d+)', output)
                    if version_match:
                        result['cuda']['version'] = version_match.group(1)
            else:
                success, output = self._execute_command('which nvcc')
                if success:
                    result['cuda']['supported'] = True
                    version_success, version_output = self._execute_command('nvcc --version')
                    if version_success:
                        version_match = re.search(r'release (\d+\.\d+)', version_output)
                        if version_match:
                            result['cuda']['version'] = version_match.group(1)
            
            # 检测OpenCL
            try:
                import pyopencl
                result['opencl']['supported'] = True
                result['opencl']['version'] = pyopencl.VERSION_TEXT
                
                # 获取OpenCL平台和设备信息
                platforms = pyopencl.get_platforms()
                platform_info = []
                for plat in platforms:  # 避免与platform模块命名冲突
                    devices = plat.get_devices()
                    device_info = []
                    for device in devices:
                        device_info.append({
                            'name': device.name,
                            'type': str(device.type),
                            'version': device.version,
                            'max_compute_units': device.max_compute_units
                        })
                    platform_info.append({
                        'name': plat.name,
                        'vendor': plat.vendor,
                        'version': plat.version,
                        'devices': device_info
                    })
                result['opencl']['details']['platforms'] = platform_info
            except ImportError:
                pass
        
        except Exception as e:
            logger.error(f"检测GPU加速支持时出错: {str(e)}")
        
        return result
    
    def detect_all_features(self, use_cache: bool = True) -> Dict:
        """检测所有CPU加速特性"""
        start_time = time.time()
        result = {
            'cpu_info': {},
            'simd': {},
            'parallel_frameworks': {},
            'python_accelerations': {},
            'gpu_acceleration': {},
            'timestamp': datetime.now().isoformat(),
            'detection_time': 0
        }
        
        # 检查缓存
        if use_cache:
            cached_data = self._load_from_cache()
            if cached_data:
                logger.info("使用缓存数据")
                return cached_data
        
        # 使用线程池并行检测
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self._detect_cpu_info): 'cpu_info',
                executor.submit(self._detect_simd): 'simd',
                executor.submit(self._detect_parallel_frameworks): 'parallel_frameworks',
                executor.submit(self._detect_python_accelerations): 'python_accelerations',
                executor.submit(self._detect_gpu_acceleration): 'gpu_acceleration'
            }
            
            for future in as_completed(futures, timeout=self.detection_timeout):
                try:
                    key = futures[future]
                    result[key] = future.result()
                except FutureTimeoutError:
                    logger.warning(f"检测 {futures[future]} 超时")
                    result[futures[future]] = {'error': '检测超时'}
                except Exception as e:
                    logger.error(f"检测 {futures[future]} 时出错: {str(e)}")
                    result[futures[future]] = {'error': str(e)}
        
        # 计算检测耗时
        result['detection_time'] = round(time.time() - start_time, 2)
        
        # 保存到缓存
        self._save_to_cache(result)
        
        return result

class CPUAccelerationGUI:
    """CPU加速检测GUI界面"""
    
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("CPU加速方案检测工具")
        self.root.geometry("900x700")
        self.root.minsize(800, 600)
        
        # 初始化检测器
        self.detector = CPUAccelerationDetector()
        
        # 创建UI
        self._create_widgets()
        
        # 设置样式
        self._setup_styles()
        
        # 初始化检测结果
        self.detection_result = None
        
        # 日志记录
        logger.info("GUI初始化完成")
    
    def _setup_styles(self):
        """设置UI样式"""
        style = ttk.Style()
        style.configure('TFrame', background='#f0f0f0')
        style.configure('TLabel', background='#f0f0f0', font=('微软雅黑', 10))
        style.configure('Title.TLabel', font=('微软雅黑', 14, 'bold'))
        style.configure('Subtitle.TLabel', font=('微软雅黑', 11, 'bold'))
        style.configure('TButton', font=('微软雅黑', 10))
        style.configure('TNotebook', background='#f0f0f0')
        style.configure('TNotebook.Tab', font=('微软雅黑', 10))
        style.configure('Treeview', font=('微软雅黑', 10), rowheight=25)
        style.configure('Treeview.Heading', font=('微软雅黑', 10, 'bold'))
        style.map('TButton', background=[('active', '#e0e0e0')])
    
    def _create_widgets(self):
        """创建UI组件"""
        # 主框架
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # 标题
        title_frame = ttk.Frame(main_frame)
        title_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(
            title_frame, 
            text="CPU加速方案检测工具", 
            style='Title.TLabel'
        ).pack(side=tk.LEFT)
        
        # 控制按钮
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Button(
            button_frame, 
            text="开始检测", 
            command=self._start_detection,
            width=15
        ).pack(side=tk.LEFT, padx=(0, 10))
        
        ttk.Button(
            button_frame, 
            text="导出报告", 
            command=self._export_report,
            width=15
        ).pack(side=tk.LEFT, padx=(0, 10))
        
        ttk.Button(
            button_frame, 
            text="清空结果", 
            command=self._clear_results,
            width=15
        ).pack(side=tk.LEFT, padx=(0, 10))
        
        ttk.Button(
            button_frame, 
            text="退出", 
            command=self._exit_app,
            width=15
        ).pack(side=tk.RIGHT)
        
        # 状态栏
        self.status_var = tk.StringVar()
        self.status_var.set("准备就绪")
        
        status_bar = ttk.Frame(main_frame)
        status_bar.pack(fill=tk.X, pady=(10, 0))
        
        ttk.Label(
            status_bar, 
            textvariable=self.status_var,
            relief=tk.SUNKEN,
            anchor=tk.W,
            padding=5
        ).pack(fill=tk.X)
        
        # 结果显示区域
        self.notebook = ttk.Notebook(main_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        
        # CPU信息标签页
        self.cpu_info_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.cpu_info_frame, text="CPU信息")
        self._create_cpu_info_tab()
        
        # SIMD指令集标签页
        self.simd_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.simd_frame, text="SIMD指令集")
        self._create_simd_tab()
        
        # 并行框架标签页
        self.parallel_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.parallel_frame, text="并行框架")
        self._create_parallel_tab()
        
        # Python加速标签页
        self.python_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.python_frame, text="Python加速")
        self._create_python_tab()
        
        # GPU加速标签页
        self.gpu_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.gpu_frame, text="GPU加速")
        self._create_gpu_tab()
        
        # 日志标签页
        self.log_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.log_frame, text="日志")
        self._create_log_tab()
    
    def _create_cpu_info_tab(self):
        """创建CPU信息标签页"""
        # 基本信息框架
        info_frame = ttk.LabelFrame(self.cpu_info_frame, text="基本信息")
        info_frame.pack(fill=tk.X, padx=5, pady=5)
        
        self.cpu_info_tree = ttk.Treeview(
            info_frame, 
            columns=('value'), 
            show='tree headings',
            height=5
        )
        self.cpu_info_tree.heading('#0', text='属性')
        self.cpu_info_tree.heading('value', text='值')
        self.cpu_info_tree.column('#0', width=150, anchor=tk.W)
        self.cpu_info_tree.column('value', width=600, anchor=tk.W)
        self.cpu_info_tree.pack(fill=tk.X, padx=5, pady=5)
        
        # 详细信息框架
        detail_frame = ttk.LabelFrame(self.cpu_info_frame, text="详细信息")
        detail_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.cpu_detail_text = tk.Text(
            detail_frame, 
            wrap=tk.WORD, 
            font=('微软雅黑', 10)
        )
        scrollbar = ttk.Scrollbar(
            detail_frame, 
            orient=tk.VERTICAL, 
            command=self.cpu_detail_text.yview
        )
        self.cpu_detail_text.configure(yscrollcommand=scrollbar.set)
        
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.cpu_detail_text.pack(fill=tk.BOTH, expand=True)
        
        # 初始占位文本
        self.cpu_detail_text.insert(tk.END, '点击"开始检测"按钮获取CPU详细信息...')
        self.cpu_detail_text.configure(state=tk.DISABLED)
    
    def _create_simd_tab(self):
        """创建SIMD指令集标签页"""
        # SSE框架
        sse_frame = ttk.LabelFrame(self.simd_frame, text="SSE指令集")
        sse_frame.pack(fill=tk.X, padx=5, pady=5)
        
        self.sse_tree = ttk.Treeview(
            sse_frame, 
            columns=('supported'), 
            show='tree headings',
            height=5
        )
        self.sse_tree.heading('#0', text='指令集')
        self.sse_tree.heading('supported', text='是否支持')
        self.sse_tree.column('#0', width=150, anchor=tk.W)
        self.sse_tree.column('supported', width=100, anchor=tk.W)
        self.sse_tree.pack(fill=tk.X, padx=5, pady=5)
        
        # AVX框架
        avx_frame = ttk.LabelFrame(self.simd_frame, text="AVX指令集")
        avx_frame.pack(fill=tk.X, padx=5, pady=5)
        
        self.avx_tree = ttk.Treeview(
            avx_frame, 
            columns=('supported'), 
            show='tree headings',
            height=5
        )
        self.avx_tree.heading('#0', text='指令集')
        self.avx_tree.heading('supported', text='是否支持')
        self.avx_tree.column('#0', width=150, anchor=tk.W)
        self.avx_tree.column('supported', width=100, anchor=tk.W)
        self.avx_tree.pack(fill=tk.X, padx=5, pady=5)
        
        # 其他指令集框架
        other_frame = ttk.LabelFrame(self.simd_frame, text="其他指令集")
        other_frame.pack(fill=tk.X, padx=5, pady=5)
        
        self.other_tree = ttk.Treeview(
            other_frame, 
            columns=('supported'), 
            show='tree headings',
            height=3
        )
        self.other_tree.heading('#0', text='指令集')
        self.other_tree.heading('supported', text='是否支持')
        self.other_tree.column('#0', width=150, anchor=tk.W)
        self.other_tree.column('supported', width=100, anchor=tk.W)
        self.other_tree.pack(fill=tk.X, padx=5, pady=5)
        
        # 详细信息框架
        detail_frame = ttk.LabelFrame(self.simd_frame, text="详细信息")
        detail_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.simd_detail_text = tk.Text(
            detail_frame, 
            wrap=tk.WORD, 
            font=('微软雅黑', 10)
        )
        scrollbar = ttk.Scrollbar(
            detail_frame, 
            orient=tk.VERTICAL, 
            command=self.simd_detail_text.yview
        )
        self.simd_detail_text.configure(yscrollcommand=scrollbar.set)
        
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.simd_detail_text.pack(fill=tk.BOTH, expand=True)
        
        # 初始占位文本
        self.simd_detail_text.insert(tk.END, '点击"开始检测"按钮获取SIMD指令集详细信息...')
        self.simd_detail_text.configure(state=tk.DISABLED)
    
    def _create_parallel_tab(self):
        """创建并行框架标签页"""
        # 并行框架表格
        self.parallel_tree = ttk.Treeview(
            self.parallel_frame, 
            columns=('supported', 'version'), 
            show='tree headings',
            height=8
        )
        self.parallel_tree.heading('#0', text='框架名称')
        self.parallel_tree.heading('supported', text='是否支持')
        self.parallel_tree.heading('version', text='版本')
        self.parallel_tree.column('#0', width=200, anchor=tk.W)
        self.parallel_tree.column('supported', width=100, anchor=tk.W)
        self.parallel_tree.column('version', width=150, anchor=tk.W)
        self.parallel_tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # 详细信息框架
        detail_frame = ttk.LabelFrame(self.parallel_frame, text="详细信息")
        detail_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.parallel_detail_text = tk.Text(
            detail_frame, 
            wrap=tk.WORD, 
            font=('微软雅黑', 10)
        )
        scrollbar = ttk.Scrollbar(
            detail_frame, 
            orient=tk.VERTICAL, 
            command=self.parallel_detail_text.yview
        )
        self.parallel_detail_text.configure(yscrollcommand=scrollbar.set)
        
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.parallel_detail_text.pack(fill=tk.BOTH, expand=True)
        
        # 初始占位文本
        self.parallel_detail_text.insert(tk.END, '点击"开始检测"按钮获取并行框架详细信息...')
        self.parallel_detail_text.configure(state=tk.DISABLED)
    
    def _create_python_tab(self):
        """创建Python加速标签页"""
        # Python加速框架表格
        self.python_tree = ttk.Treeview(
            self.python_frame, 
            columns=('supported', 'version'), 
            show='tree headings',
            height=8
        )
        self.python_tree.heading('#0', text='库名称')
        self.python_tree.heading('supported', text='是否支持')
        self.python_tree.heading('version', text='版本')
        self.python_tree.column('#0', width=200, anchor=tk.W)
        self.python_tree.column('supported', width=100, anchor=tk.W)
        self.python_tree.column('version', width=150, anchor=tk.W)
        self.python_tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # 详细信息框架
        detail_frame = ttk.LabelFrame(self.python_frame, text="详细信息")
        detail_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.python_detail_text = tk.Text(
            detail_frame, 
            wrap=tk.WORD, 
            font=('微软雅黑', 10)
        )
        scrollbar = ttk.Scrollbar(
            detail_frame, 
            orient=tk.VERTICAL, 
            command=self.python_detail_text.yview
        )
        self.python_detail_text.configure(yscrollcommand=scrollbar.set)
        
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.python_detail_text.pack(fill=tk.BOTH, expand=True)
        
        # 初始占位文本
        self.python_detail_text.insert(tk.END, '点击"开始检测"按钮获取Python加速库详细信息...')
        self.python_detail_text.configure(state=tk.DISABLED)
    
    def _create_gpu_tab(self):
        """创建GPU加速标签页"""
        # GPU加速框架表格
        self.gpu_tree = ttk.Treeview(
            self.gpu_frame, 
            columns=('supported', 'version'), 
            show='tree headings',
            height=8
        )
        self.gpu_tree.heading('#0', text='技术名称')
        self.gpu_tree.heading('supported', text='是否支持')
        self.gpu_tree.heading('version', text='版本')
        self.gpu_tree.column('#0', width=200, anchor=tk.W)
        self.gpu_tree.column('supported', width=100, anchor=tk.W)
        self.gpu_tree.column('version', width=150, anchor=tk.W)
        self.gpu_tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # 详细信息框架
        detail_frame = ttk.LabelFrame(self.gpu_frame, text="详细信息")
        detail_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.gpu_detail_text = tk.Text(
            detail_frame, 
            wrap=tk.WORD, 
            font=('微软雅黑', 10)
        )
        scrollbar = ttk.Scrollbar(
            detail_frame, 
            orient=tk.VERTICAL, 
            command=self.gpu_detail_text.yview
        )
        self.gpu_detail_text.configure(yscrollcommand=scrollbar.set)
        
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.gpu_detail_text.pack(fill=tk.BOTH, expand=True)
        
        # 初始占位文本
        self.gpu_detail_text.insert(tk.END, '点击"开始检测"按钮获取GPU加速详细信息...')
        self.gpu_detail_text.configure(state=tk.DISABLED)
    
    def _create_log_tab(self):
        """创建日志标签页"""
        self.log_text = tk.Text(
            self.log_frame, 
            wrap=tk.WORD, 
            font=('微软雅黑', 10)
        )
        scrollbar = ttk.Scrollbar(
            self.log_frame, 
            orient=tk.VERTICAL, 
            command=self.log_text.yview
        )
        self.log_text.configure(yscrollcommand=scrollbar.set)
        
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        
        # 重定向日志输出
        class LogHandler(logging.Handler):
            def __init__(self, text_widget):
                super().__init__()
                self.text_widget = text_widget
            
            def emit(self, record):
                msg = self.format(record)
                self.text_widget.configure(state=tk.NORMAL)
                self.text_widget.insert(tk.END, msg + '\n')
                self.text_widget.configure(state=tk.DISABLED)
                self.text_widget.see(tk.END)
        
        log_handler = LogHandler(self.log_text)
        log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(log_handler)
        
        # 初始日志
        logger.info("日志系统初始化完成")
    
    def _start_detection(self):
        """开始检测CPU加速特性"""
        self.status_var.set("正在检测CPU加速特性...")
        self.root.update()
        
        try:
            # 禁用按钮防止重复点击
            for widget in self.root.winfo_children():
                if isinstance(widget, ttk.Button):
                    widget.configure(state=tk.DISABLED)
            
            # 在新线程中执行检测，防止GUI冻结
            def detection_thread():
                try:
                    # 执行检测
                    self.detection_result = self.detector.detect_all_features()
                    
                    # 更新UI
                    self.root.after(0, self._update_ui_with_results)
                    
                    self.status_var.set(f"检测完成，耗时 {self.detection_result['detection_time']} 秒")
                    logger.info("CPU加速特性检测完成")
                except Exception as e:
                    self.root.after(0, lambda: messagebox.showerror("错误", f"检测过程中出错: {str(e)}"))
                    logger.error(f"检测过程中出错: {str(e)}")
                    logger.error(traceback.format_exc())
                    self.status_var.set("检测出错")
                finally:
                    # 重新启用按钮
                    self.root.after(0, lambda: [widget.configure(state=tk.NORMAL) 
                                              for widget in self.root.winfo_children() 
                                              if isinstance(widget, ttk.Button)])
            
            threading.Thread(target=detection_thread, daemon=True).start()
        
        except Exception as e:
            messagebox.showerror("错误", f"启动检测线程时出错: {str(e)}")
            logger.error(f"启动检测线程时出错: {str(e)}")
            logger.error(traceback.format_exc())
            self.status_var.set("检测出错")
            
            # 重新启用按钮
            for widget in self.root.winfo_children():
                if isinstance(widget, ttk.Button):
                    widget.configure(state=tk.NORMAL)
    
    def _update_ui_with_results(self):
        """用检测结果更新UI"""
        if not self.detection_result:
            return
        
        # 更新CPU信息标签页
        self._update_cpu_info_tab()
        
        # 更新SIMD指令集标签页
        self._update_simd_tab()
        
        # 更新并行框架标签页
        self._update_parallel_tab()
        
        # 更新Python加速标签页
        self._update_python_tab()
        
        # 更新GPU加速标签页
        self._update_gpu_tab()
    
    def _update_cpu_info_tab(self):
        """更新CPU信息标签页"""
        cpu_info = self.detection_result.get('cpu_info', {})
        
        # 清空现有数据
        for item in self.cpu_info_tree.get_children():
            self.cpu_info_tree.delete(item)
        
        # 添加新数据
        self.cpu_info_tree.insert('', tk.END, text='CPU名称', values=(cpu_info.get('cpu_name', '未知')))
        self.cpu_info_tree.insert('', tk.END, text='物理核心数', values=(cpu_info.get('cpu_cores', '未知')))
        self.cpu_info_tree.insert('', tk.END, text='逻辑线程数', values=(cpu_info.get('cpu_threads', '未知')))
        self.cpu_info_tree.insert('', tk.END, text='系统架构', values=(cpu_info.get('architecture', '未知')))
        self.cpu_info_tree.insert('', tk.END, text='操作系统', values=(cpu_info.get('system', '未知')))
        self.cpu_info_tree.insert('', tk.END, text='平台信息', values=(cpu_info.get('platform', '未知')))
        
        # 更新详细信息文本
        self.cpu_detail_text.configure(state=tk.NORMAL)
        self.cpu_detail_text.delete(1.0, tk.END)
        self.cpu_detail_text.insert(tk.END, json.dumps(cpu_info, indent=2, ensure_ascii=False))
        self.cpu_detail_text.configure(state=tk.DISABLED)
    
    def _update_simd_tab(self):
        """更新SIMD指令集标签页"""
        simd_info = self.detection_result.get('simd', {})
        
        # 清空现有数据
        for item in self.sse_tree.get_children():
            self.sse_tree.delete(item)
        for item in self.avx_tree.get_children():
            self.avx_tree.delete(item)
        for item in self.other_tree.get_children():
            self.other_tree.delete(item)
        
        # 更新SSE指令集
        sse_info = simd_info.get('sse', {})
        for version in ['sse', 'sse2', 'sse3', 'ssse3', 'sse4.1', 'sse4.2']:
            supported = sse_info.get('details', {}).get(version, False)
            self.sse_tree.insert('', tk.END, text=version.upper(), 
                                values=('是' if supported else '否'))
        
        # 更新AVX指令集
        avx_info = simd_info.get('avx', {})
        for version in ['avx', 'avx2', 'avx512']:
            supported = avx_info.get('details', {}).get(version, False)
            self.avx_tree.insert('', tk.END, text=version.upper(), 
                                values=('是' if supported else '否'))
        
        # 更新其他指令集
        self.other_tree.insert('', tk.END, text='FMA', 
                              values=('是' if simd_info.get('fma', {}).get('supported', False) else '否'))
        self.other_tree.insert('', tk.END, text='NEON', 
                              values=('是' if simd_info.get('neon', {}).get('supported', False) else '否'))
        
        # 更新详细信息文本
        self.simd_detail_text.configure(state=tk.NORMAL)
        self.simd_detail_text.delete(1.0, tk.END)
        self.simd_detail_text.insert(tk.END, json.dumps(simd_info, indent=2, ensure_ascii=False))
        self.simd_detail_text.configure(state=tk.DISABLED)
    
    def _update_parallel_tab(self):
        """更新并行框架标签页"""
        parallel_info = self.detection_result.get('parallel_frameworks', {})
        
        # 清空现有数据
        for item in self.parallel_tree.get_children():
            self.parallel_tree.delete(item)
        
        # 更新并行框架信息
        self.parallel_tree.insert('', tk.END, text='OpenMP', 
                                 values=('是' if parallel_info.get('openmp', {}).get('supported', False) else '否',
                                         parallel_info.get('openmp', {}).get('version', '未知')))
        self.parallel_tree.insert('', tk.END, text='OpenMPI', 
                                 values=('是' if parallel_info.get('openmpi', {}).get('supported', False) else '否',
                                         parallel_info.get('openmpi', {}).get('version', '未知')))
        self.parallel_tree.insert('', tk.END, text='Intel TBB', 
                                 values=('是' if parallel_info.get('tbb', {}).get('supported', False) else '否',
                                         parallel_info.get('tbb', {}).get('version', '未知')))
        
        # 更新详细信息文本
        self.parallel_detail_text.configure(state=tk.NORMAL)
        self.parallel_detail_text.delete(1.0, tk.END)
        self.parallel_detail_text.insert(tk.END, json.dumps(parallel_info, indent=2, ensure_ascii=False))
        self.parallel_detail_text.configure(state=tk.DISABLED)
    
    def _update_python_tab(self):
        """更新Python加速标签页"""
        python_info = self.detection_result.get('python_accelerations', {})
        
        # 清空现有数据
        for item in self.python_tree.get_children():
            self.python_tree.delete(item)
        
        # 更新Python加速库信息
        self.python_tree.insert('', tk.END, text='NumPy', 
                               values=('是' if python_info.get('numpy', {}).get('supported', False) else '否',
                                       python_info.get('numpy', {}).get('version', '未知')))
        self.python_tree.insert('', tk.END, text='Numba', 
                               values=('是' if python_info.get('numba', {}).get('supported', False) else '否',
                                       python_info.get('numba', {}).get('version', '未知')))
        self.python_tree.insert('', tk.END, text='Cython', 
                               values=('是' if python_info.get('cython', {}).get('supported', False) else '否',
                                       python_info.get('cython', {}).get('version', '未知')))
        
        # 更新详细信息文本
        self.python_detail_text.configure(state=tk.NORMAL)
        self.python_detail_text.delete(1.0, tk.END)
        self.python_detail_text.insert(tk.END, json.dumps(python_info, indent=2, ensure_ascii=False))
        self.python_detail_text.configure(state=tk.DISABLED)
    
    def _update_gpu_tab(self):
        """更新GPU加速标签页"""
        gpu_info = self.detection_result.get('gpu_acceleration', {})
        
        # 清空现有数据
        for item in self.gpu_tree.get_children():
            self.gpu_tree.delete(item)
        
        # 更新GPU加速信息
        self.gpu_tree.insert('', tk.END, text='CUDA', 
                            values=('是' if gpu_info.get('cuda', {}).get('supported', False) else '否',
                                    gpu_info.get('cuda', {}).get('version', '未知')))
        self.gpu_tree.insert('', tk.END, text='OpenCL', 
                            values=('是' if gpu_info.get('opencl', {}).get('supported', False) else '否',
                                    gpu_info.get('opencl', {}).get('version', '未知')))
        
        # 更新详细信息文本
        self.gpu_detail_text.configure(state=tk.NORMAL)
        self.gpu_detail_text.delete(1.0, tk.END)
        self.gpu_detail_text.insert(tk.END, json.dumps(gpu_info, indent=2, ensure_ascii=False))
        self.gpu_detail_text.configure(state=tk.DISABLED)
    
    def _export_report(self):
        """导出检测报告"""
        if not self.detection_result:
            messagebox.showwarning("警告", "请先执行检测再导出报告")
            return
        
        try:
            # 选择保存位置
            file_path = filedialog.asksaveasfilename(
                defaultextension=".json",
                filetypes=[("JSON文件", "*.json"), ("文本文件", "*.txt"), ("所有文件", "*.*")],
                title="保存检测报告"
            )
            
            if not file_path:
                return  # 用户取消了保存
            
            # 保存报告
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(self.detection_result, f, indent=2, ensure_ascii=False)
            
            messagebox.showinfo("成功", f"报告已成功导出到: {file_path}")
            logger.info(f"检测报告已导出到: {file_path}")
        
        except Exception as e:
            messagebox.showerror("错误", f"导出报告时出错: {str(e)}")
            logger.error(f"导出报告时出错: {str(e)}")
            logger.error(traceback.format_exc())
    
    def _clear_results(self):
        """清空检测结果"""
        self.detection_result = None
        
        # 清空所有表格和文本
        for tree in [self.cpu_info_tree, self.sse_tree, self.avx_tree, self.other_tree, 
                     self.parallel_tree, self.python_tree, self.gpu_tree]:
            for item in tree.get_children():
                tree.delete(item)
        
        for text in [self.cpu_detail_text, self.simd_detail_text, 
                     self.parallel_detail_text, self.python_detail_text, 
                     self.gpu_detail_text]:
            text.configure(state=tk.NORMAL)
            text.delete(1.0, tk.END)
            text.insert(tk.END, '点击"开始检测"按钮获取详细信息...')
            text.configure(state=tk.DISABLED)
        
        self.status_var.set("结果已清空")
        logger.info("检测结果已清空")
    
    def _exit_app(self):
        """退出应用程序"""
        self.root.quit()
        self.root.destroy()
        logger.info("应用程序已退出")

def main():
    """主函数"""
    try:
        root = tk.Tk()
        app = CPUAccelerationGUI(root)
        root.mainloop()
    except Exception as e:
        logger.critical(f"应用程序崩溃: {str(e)}")
        logger.critical(traceback.format_exc())
        messagebox.showerror("错误", f"应用程序崩溃: {str(e)}")

if __name__ == "__main__":
    main()