import asyncio
import csv
import io
import math
from pathlib import Path
import time
from collections import deque
import psutil


RAM_ACCOUNTING = 'physical_including_cache_v1'


def system_memory(vm=None):
    """Physical occupancy, not memory pressure. Linux cached includes reclaimable slab."""
    vm = psutil.virtual_memory() if vm is None else vm
    occupied = max(0, vm.total - vm.free)
    cache = min(occupied, max(0, vm.cached + vm.buffers - vm.shared))
    return dict(used_bytes=occupied, total_bytes=vm.total, free_bytes=vm.free,
                available_bytes=vm.available, cache_bytes=cache,
                non_cache_bytes=occupied - cache, accounting=RAM_ACCOUNTING)


def process_memory(pid, proc_root=Path('/proc')):
    """Resident engine + worker memory, proportionally counting shared pages (PSS)."""
    try:
        root = psutil.Process(pid)
        processes = [root, *root.children(recursive=True)]
        totals = dict(resident_bytes=0, anonymous_bytes=0, file_bytes=0, shared_bytes=0, swap_bytes=0)
        fields = dict(resident_bytes='Pss', anonymous_bytes='Pss_Anon', file_bytes='Pss_File',
                      shared_bytes='Pss_Shmem', swap_bytes='SwapPss')
        for process in processes:
            try:
                content = (proc_root / str(process.pid) / 'smaps_rollup').read_text()
            except FileNotFoundError:
                if process.pid == pid:
                    return None
                continue  # A worker exited during the snapshot.
            values = {parts[0].rstrip(':'): int(parts[1]) * 1024
                      for line in content.splitlines() if len(parts := line.split()) == 3 and parts[2] == 'kB'}
            for key, field in fields.items():
                if field not in values:
                    return None  # Never represent an unreadable measurement as zero.
                totals[key] += values[field]
        if not root.is_running():
            return None
        return dict(**totals, pid=pid, sampled_at=time.time(), accounting='process_tree_pss')
    except (OSError, ValueError, psutil.Error):
        return None


def memory_peaks(samples):
    def peak(section, field):
        values = [(s.get(section) or {}).get(field) for s in samples]
        return max((v for v in values if type(v) in (int, float) and math.isfinite(v)), default=None)
    return dict(ram_bytes=peak('ram', 'used_bytes'), ram_accounting=RAM_ACCOUNTING,
                ram_cache_bytes=peak('ram', 'cache_bytes'), ram_non_cache_bytes=peak('ram', 'non_cache_bytes'),
                model_ram_bytes=peak('model_memory', 'resident_bytes'),
                model_file_bytes=peak('model_memory', 'file_bytes'), model_ram_accounting='process_tree_pss')


def annotate_memory(value):
    """Label historical exports without inventing missing cache or resident measurements."""
    if isinstance(value, dict):
        if isinstance(value.get('hardware_peaks'), dict):
            value['hardware_peaks'].setdefault('ram_accounting', 'legacy_total_minus_available')
        if isinstance(value.get('ram'), dict) and 'used_bytes' in value['ram']:
            value['ram'].setdefault('accounting', 'legacy_total_minus_available')
        for child in value.values():
            annotate_memory(child)
    elif isinstance(value, list):
        for child in value:
            annotate_memory(child)
    return value


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


class CpuPower:
    """Read package power without changing permissions or guessing from CPU load."""
    def __init__(self, sysfs=Path('/sys')):
        self.sysfs = Path(sysfs)
        self.previous = {}

    def sample(self, now=None):
        now = time.monotonic() if now is None else now
        readings, packages = [], 0
        for zone in (self.sysfs / 'class/powercap').glob('intel-rapl:*'):
            try:
                if not (zone / 'name').read_text().strip().startswith('package-'):
                    continue
                packages += 1
                energy = int((zone / 'energy_uj').read_text())
                maximum = int((zone / 'max_energy_range_uj').read_text())
                previous = self.previous.get(str(zone))
                self.previous[str(zone)] = (now, energy)
                if previous and now > previous[0]:
                    delta = energy - previous[1]
                    if delta < 0:
                        delta += maximum
                    watts = delta / 1e6 / (now - previous[0])
                    if 0 <= watts < 2000:
                        readings.append(watts)
            except (OSError, ValueError):
                continue
        if packages and len(readings) == packages:
            return dict(watts=sum(readings), source='Linux RAPL', detail='CPU package · sample average', error=None)

        # On Raphael (Ryzen 7000), amdgpu's PPT sensor reports SoC/package
        # power, including the CPU. Never treat an arbitrary discrete GPU's
        # power reading as CPU power. See docs.kernel.org/gpu/amdgpu/thermal.html.
        for sensor in (self.sysfs / 'class/hwmon').glob('hwmon*'):
            try:
                if (sensor / 'name').read_text().strip() != 'amdgpu':
                    continue
                if (sensor / 'device/vendor').read_text().strip() != '0x1002':
                    continue
                if (sensor / 'device/device').read_text().strip() != '0x164e':
                    continue
                if (sensor / 'power1_label').read_text().strip() != 'PPT':
                    continue
                watts = number((sensor / 'power1_input').read_text())
                if watts is not None and 0 <= watts < 2e9:
                    return dict(watts=watts / 1e6, source='AMD package PPT',
                                detail='CPU package · includes SoC / integrated graphics', error=None)
            except OSError:
                continue
        return dict(watts=None, source=None, detail='Package sensor unavailable',
                    error='No readable CPU package-power sensor. Values are never estimated from CPU usage.')


def parse_gpu(output):
    row = next(csv.reader(io.StringIO(output)), [])
    if len(row) != 9:
        raise ValueError('GPU metrics unavailable')
    name, used, total, util, watts, limit, temp, driver, pcie = [v.strip() for v in row]
    return dict(name=name, used_bytes=number(used) * 2**20 if number(used) is not None else None,
                total_bytes=number(total) * 2**20 if number(total) is not None else None,
                utilization=number(util), power_watts=number(watts), power_limit_watts=number(limit),
                temperature_c=number(temp), driver=driver, pcie_generation=number(pcie))


class Telemetry:
    def __init__(self, engine_pid=lambda: None):
        self.value = {'cpu_percent': None, 'gpu': None, 'ram': None, 'timestamp': None}
        self.history = deque(maxlen=3600)
        self.cpu_power = CpuPower()
        self.engine_pid = engine_pid
        self.model_memory = None

    async def sample_model(self):
        pid = self.engine_pid()
        previous = self.model_memory
        if not pid:
            self.model_memory = None
        elif not previous or previous['pid'] != pid or time.time() - previous['sampled_at'] >= 5:
            measured = await asyncio.to_thread(process_memory, pid)
            self.model_memory = measured if self.engine_pid() == pid else None
        return self.model_memory

    async def run(self):
        psutil.cpu_percent()
        while True:
            data = dict(cpu_percent=psutil.cpu_percent(), cpu_count=psutil.cpu_count(),
                        cpu_power=self.cpu_power.sample(),
                        ram=system_memory(), model_memory=await self.sample_model(),
                        gpu=None, timestamp=time.time(), gpu_error=None)
            proc = None
            try:
                proc = await asyncio.create_subprocess_exec('nvidia-smi', '--id=0',
                    '--query-gpu=name,memory.used,memory.total,utilization.gpu,power.draw,power.limit,temperature.gpu,driver_version,pcie.link.gen.current',
                    '--format=csv,noheader,nounits', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                out, err = await asyncio.wait_for(proc.communicate(), 3)
                if proc.returncode:
                    raise ValueError(err.decode().strip() or out.decode().strip())
                data['gpu'] = parse_gpu(out.decode())
            except asyncio.CancelledError:
                if proc and proc.returncode is None:
                    proc.kill()
                    await proc.communicate()
                raise
            except (OSError, ValueError, asyncio.TimeoutError) as exc:
                data['gpu_error'] = str(exc) or 'GPU query timed out'
                if proc and proc.returncode is None:
                    proc.kill()
                    await proc.wait()
            self.value = data
            self.history.append(data)
            await asyncio.sleep(1)
