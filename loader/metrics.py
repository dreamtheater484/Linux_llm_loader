import asyncio
import csv
import io
import math
from pathlib import Path
import time
from collections import deque
import psutil


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
    def __init__(self):
        self.value = {'cpu_percent': None, 'gpu': None, 'ram': None, 'timestamp': None}
        self.history = deque(maxlen=3600)
        self.cpu_power = CpuPower()

    async def run(self):
        psutil.cpu_percent()
        while True:
            vm = psutil.virtual_memory()
            data = dict(cpu_percent=psutil.cpu_percent(), cpu_count=psutil.cpu_count(),
                        cpu_power=self.cpu_power.sample(),
                        ram={'used_bytes': vm.total - vm.available, 'total_bytes': vm.total, 'available_bytes': vm.available},
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
