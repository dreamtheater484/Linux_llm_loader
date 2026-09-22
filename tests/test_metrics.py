import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

from loader import metrics

GiB = 2**30


def test_mapped_model_cache_is_counted_as_physical_ram():
    vm = SimpleNamespace(total=183*GiB, free=47*GiB, available=170*GiB,
                         cached=125*GiB, buffers=GiB, shared=2*GiB)
    ram = metrics.system_memory(vm)
    assert ram['used_bytes'] == 136*GiB  # Not the previous 13 GiB.
    assert ram['cache_bytes'] == 124*GiB
    assert ram['non_cache_bytes'] == 12*GiB
    assert ram['cache_bytes'] + ram['non_cache_bytes'] + ram['free_bytes'] == ram['total_bytes']
    assert ram['available_bytes'] == 170*GiB  # Still correct for admission/headroom.
    vm.shared = 200*GiB
    assert metrics.system_memory(vm)['cache_bytes'] == 0
    vm.shared = 0
    vm.cached = 200*GiB
    assert metrics.system_memory(vm)['non_cache_bytes'] == 0


def test_engine_workers_count_shared_pages_proportionally(tmp_path, monkeypatch):
    root, worker = Mock(pid=10), Mock(pid=11)
    root.children.return_value = [worker]
    root.is_running.return_value = True
    monkeypatch.setattr(metrics.psutil, 'Process', lambda pid:root)
    for pid in (10, 11):
        folder = tmp_path / str(pid)
        folder.mkdir()
        # Each worker maps the same 1000 KiB file; PSS allocates half to each.
        (folder/'smaps_rollup').write_text('1000-2000 ---p 0 00:00 0 [rollup]\nRss: 1100 kB\nPss: 600 kB\nPss_Anon: 100 kB\nPss_File: 500 kB\nPss_Shmem: 0 kB\nSwapPss: 7 kB\n')
    result = metrics.process_memory(10, tmp_path)
    assert result['resident_bytes'] == 1200*1024
    assert result['file_bytes'] == 1000*1024
    assert result['anonymous_bytes'] == 200*1024
    assert result['swap_bytes'] == 14*1024
    (tmp_path/'11/smaps_rollup').unlink()
    assert metrics.process_memory(10, tmp_path)['resident_bytes'] == 600*1024
    (tmp_path/'10/smaps_rollup').write_text('Rss: 1100 kB\n')
    assert metrics.process_memory(10, tmp_path) is None
    (tmp_path/'10/smaps_rollup').unlink()
    assert metrics.process_memory(10, tmp_path) is None


def test_model_switch_or_unload_never_reuses_previous_memory(monkeypatch):
    pid = [10]
    telemetry = metrics.Telemetry(lambda:pid[0])
    called = []
    def sample(value):
        called.append(value)
        return dict(pid=value, sampled_at=metrics.time.time(), resident_bytes=value)
    monkeypatch.setattr(metrics, 'process_memory', sample)
    async def check():
        assert (await telemetry.sample_model())['pid'] == 10
        assert (await telemetry.sample_model())['pid'] == 10
        assert called == [10]
        pid[0] = 20
        assert (await telemetry.sample_model())['pid'] == 20
        pid[0] = None
        assert await telemetry.sample_model() is None
        assert called == [10,20]
    asyncio.run(check())


def test_unload_during_memory_scan_discards_result(monkeypatch):
    pid = [10]
    telemetry = metrics.Telemetry(lambda:pid[0])
    def sample(value):
        pid[0] = None
        return dict(pid=value, sampled_at=metrics.time.time(), resident_bytes=100)
    monkeypatch.setattr(metrics, 'process_memory', sample)
    assert asyncio.run(telemetry.sample_model()) is None


def test_unreadable_engine_memory_is_unknown(monkeypatch):
    def denied(pid):
        raise metrics.psutil.AccessDenied(pid)
    monkeypatch.setattr(metrics.psutil, 'Process', denied)
    assert metrics.process_memory(10) is None


def test_benchmark_peaks_include_cache_and_model_residency():
    samples = [dict(ram=dict(used_bytes=136*GiB,cache_bytes=124*GiB,non_cache_bytes=12*GiB),
                    model_memory=dict(resident_bytes=113*GiB,file_bytes=110*GiB)),
               dict(ram=dict(used_bytes=137*GiB,cache_bytes=123*GiB,non_cache_bytes=14*GiB), model_memory=None)]
    peaks = metrics.memory_peaks(samples)
    assert peaks['ram_bytes'] == 137*GiB
    assert peaks['model_ram_bytes'] == 113*GiB
    assert peaks['ram_cache_bytes'] == 124*GiB
    assert peaks['ram_accounting'] == metrics.RAM_ACCOUNTING
    assert metrics.memory_peaks([])['ram_bytes'] is None


def test_legacy_archive_accounting_is_explicit_without_inventing_data():
    value = dict(hardware=dict(ram=dict(used_bytes=13)),
                 tasks=[dict(metrics=[dict(hardware_peaks=dict(ram_bytes=16))])],
                 runs=[dict(hardware_peaks=dict(ram_bytes=136,ram_accounting=metrics.RAM_ACCOUNTING))])
    metrics.annotate_memory(value)
    assert value['hardware']['ram']['accounting'] == 'legacy_total_minus_available'
    assert value['tasks'][0]['metrics'][0]['hardware_peaks'] == dict(ram_bytes=16,ram_accounting='legacy_total_minus_available')
    assert value['runs'][0]['hardware_peaks']['ram_accounting'] == metrics.RAM_ACCOUNTING
