"""Inspect NVIDIA hardware before importing any CUDA Python runtime."""
from __future__ import annotations
import csv
import ctypes
import io
import os
import re
import shutil
import subprocess


def classify_gpu(gpu: dict) -> dict:
    result = dict(gpu)
    cc = str(result.get('compute_capability', ''))
    family = re.search(r'\b(?:NVIDIA\s+)?(?:GeForce\s+)?RTX\s+(20|30|40|50)\d{2}\b', str(result.get('name', '')), re.IGNORECASE)
    expected = {'20': '7.5', '30': '8.6', '40': '8.9', '50': '12.0'}
    supported = family is not None and expected[family.group(1)] == cc
    runtime = ('cu128' if cc == '12.0' else 'cu121') if supported else ''
    result['runtime_id'] = runtime
    # An 8 GB board can expose slightly less than 8192 MiB to the driver.
    if not runtime:
        status, message = 'unsupported_architecture', '此显卡架构尚未验证，当前支持 RTX 20/30/40/50 系列。'
    elif int(result.get('total_mb', 0)) < 7500:
        status, message = 'insufficient_vram', '需要 8 GB 级别或更多显存。'
    else:
        try:
            driver = tuple(int(x) for x in str(result.get('driver', '0')).split('.')[:2])
        except ValueError:
            driver = (0,)
        minimum = (527, 41) if runtime == 'cu121' else (570, 65)
        if driver < minimum:
            status, message = 'driver_update_required', '请更新 NVIDIA 驱动后重试。'
        elif int(result.get('free_mb', 0)) < 5000:
            status, message = 'busy', '可用显存不足，请关闭其他占用显卡的程序。'
        else:
            status, message = 'compatible', '可使用 ' + runtime + ' 运行组件。'
    result.update(status=status, message=message)
    return result


def _driver_capabilities() -> dict[int, str]:
    """Fallback for older nvidia-smi without compute_cap in its query fields."""
    if os.name != 'nt':
        return {}
    try:
        cuda = ctypes.WinDLL('nvcuda.dll')
        if cuda.cuInit(0):
            return {}
        count = ctypes.c_int()
        if cuda.cuDeviceGetCount(ctypes.byref(count)):
            return {}
        result = {}
        for index in range(count.value):
            device, major, minor = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
            if not cuda.cuDeviceGet(ctypes.byref(device), index) and not cuda.cuDeviceComputeCapability(ctypes.byref(major), ctypes.byref(minor), device):
                result[index] = f'{major.value}.{minor.value}'
        return result
    except (OSError, AttributeError):
        return {}


def detect_gpus() -> list[dict]:
    executable = shutil.which('nvidia-smi')
    if not executable and os.name == 'nt':
        candidate = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'nvidia-smi.exe')
        if os.path.isfile(candidate):
            executable = candidate
    if not executable:
        return []
    columns = ['index', 'uuid', 'name', 'compute_cap', 'memory.total', 'memory.free', 'driver_version']
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    try:
        query = lambda fields: subprocess.run([executable, '--query-gpu=' + ','.join(fields), '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=12, creationflags=flags)
        process = query(columns)
        capabilities = {}
        if process.returncode:
            columns.remove('compute_cap')
            process = query(columns)
            capabilities = _driver_capabilities()
        if process.returncode:
            return []
        result = []
        for values in csv.reader(io.StringIO(process.stdout)):
            if len(values) != len(columns):
                continue
            item = dict(zip(columns, (value.strip() for value in values)))
            try:
                index = int(item['index'])
                result.append(classify_gpu(dict(index=index, uuid=item['uuid'], name=item['name'],
                    compute_capability=item.get('compute_cap', capabilities.get(index, '')),
                    total_mb=int(float(item['memory.total'])), free_mb=int(float(item['memory.free'])), driver=item['driver_version'])))
            except ValueError:
                continue
        return result
    except (OSError, subprocess.SubprocessError):
        return []


def select_gpu(gpus: list[dict] | None = None, preferred_uuid: str = '') -> dict | None:
    candidates = [gpu for gpu in (detect_gpus() if gpus is None else gpus) if gpu.get('status') == 'compatible']
    preferred = next((gpu for gpu in candidates if gpu['uuid'] == preferred_uuid), None)
    if preferred_uuid:
        return preferred
    return max(candidates, key=lambda gpu: gpu['free_mb'], default=None)
