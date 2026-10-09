"""The Linux/Windows seam, exercised with real processes, files and links."""
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

import inflect_platform as host

# A child that starts a grandchild, reports its PID, then waits. Stopping the
# child's tree must stop the grandchild too.
TREE = '''
import subprocess, sys, time
grandchild = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
print(grandchild.pid, flush=True)
time.sleep(120)
'''


def wait_dead(pid, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not host.pid_alive(pid):
            return True
        time.sleep(.1)
    return False


def test_stopping_a_tree_stops_descendants_and_nothing_else():
    bystander = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
    child = subprocess.Popen([sys.executable, '-c', TREE], stdout=subprocess.PIPE, text=True, **host.owned())
    tree = host.ProcessTree(child.pid)
    try:
        grandchild = int(child.stdout.readline())
        assert host.pid_alive(child.pid) and host.pid_alive(grandchild)
        tree.kill()
        child.wait(15)
        assert tree.wait(15)  # Returns once every process of the tree is gone, not just the child.
        assert wait_dead(grandchild, 1)
        assert host.pid_alive(bystander.pid)
    finally:
        tree.close()
        bystander.kill()
        bystander.wait()
        child.stdout.close()


def test_children_cannot_start_before_the_tree_holds_them():
    # A venv's Scripts\python.exe spawns the real interpreter at once. Even if the
    # owner is slow to build its tree, nothing may run (or escape) before it exists.
    child = subprocess.Popen([sys.executable, '-c', TREE], stdout=subprocess.PIPE, text=True, **host.owned())
    try:
        time.sleep(1)
        tree = host.ProcessTree(child.pid)
        grandchild = int(child.stdout.readline())
        tree.kill()
        child.wait(15)
        assert wait_dead(grandchild)
    finally:
        tree.close()
        child.stdout.close()


def test_shared_log_never_overwrites_another_writer(tmp_path):
    path = tmp_path / 'manager.log'
    script = 'import sys\nprint("child 1", flush=True)\nsys.stdin.readline()\nprint("child 2", flush=True)\n'
    with host.append_log(path) as log:
        child = subprocess.Popen([sys.executable, '-c', script], stdin=subprocess.PIPE, stdout=log, text=True)
    deadline = time.monotonic() + 15
    while b'child 1' not in path.read_bytes() and time.monotonic() < deadline:
        time.sleep(.05)
    with host.append_log(path) as other:
        other.write(b'restart launcher\n')
    child.communicate('go\n', timeout=15)
    assert path.read_bytes().replace(b'\r\n', b'\n') == b'child 1\nrestart launcher\nchild 2\n'


@pytest.mark.skipif(not host.WINDOWS, reason='Kill-on-close job objects are Windows-only')
def test_engine_tree_ends_when_its_manager_exits_without_cleanup():
    # The "manager" puts its child in a job, then exits abruptly without closing it.
    manager = f'''
import subprocess, sys, os
sys.path.insert(0, {str(Path(host.__file__).parent)!r})
import inflect_platform as host
child = subprocess.Popen([sys.executable, '-c', {TREE!r}], stdout=subprocess.PIPE, text=True, **host.owned())
tree = host.ProcessTree(child.pid)
print(child.pid, child.stdout.readline().strip(), flush=True)
os._exit(0)
'''
    output = subprocess.run([sys.executable, '-c', manager], capture_output=True, text=True, timeout=60).stdout.split()
    child, grandchild = map(int, output)
    assert wait_dead(child) and wait_dead(grandchild)


def test_pid_alive_never_signals():
    assert host.pid_alive(os.getpid())
    finished = subprocess.Popen([sys.executable, '-c', 'pass'])
    finished.wait()
    assert not host.pid_alive(finished.pid)


def test_exclusive_lock_is_released_on_close(tmp_path):
    path = tmp_path / 'manager.lock'
    first = path.open('a')
    host.lock(first, blocking=False)
    with path.open('a') as second:
        with pytest.raises(BlockingIOError):
            host.lock(second, blocking=False)
    first.close()
    with path.open('a') as third:
        host.lock(third, blocking=False)


def test_positional_writes_into_a_sparse_preallocated_file(tmp_path):
    path = tmp_path / 'model.part'
    size = 64 * 2**20
    with path.open('wb') as handle:
        host.make_sparse(handle.fileno())
        handle.truncate(size)
    fd = os.open(path, os.O_RDWR | host.O_BINARY)
    try:
        # Out of order, as parallel ranges finish; bytes must never be translated.
        assert host.pwrite(fd, b'tail\n', size - 5) == 5
        assert host.pwrite(fd, b'\r\nhead', 0) == 6
    finally:
        os.close(fd)
    data = path.read_bytes()
    assert len(data) == size and data[:6] == b'\r\nhead' and data[-5:] == b'tail\n'
    assert data[6:size - 5].count(0) == size - 11


def test_directory_link_switches_releases_but_never_replaces_real_folders(tmp_path):
    first, second = tmp_path / 'b1', tmp_path / 'b2'
    for folder in (first, second):
        folder.mkdir()
        (folder / 'name.txt').write_text(folder.name)
    link = tmp_path / 'bin'
    host.replace_link(link, first)
    assert (link / 'name.txt').read_text() == 'b1' and host.is_link(link)
    host.replace_link(link, second)
    assert (link / 'name.txt').read_text() == 'b2'
    assert (first / 'name.txt').read_text() == 'b1'  # The previous release stays intact.
    assert not any(p.name.startswith('.bin-') for p in tmp_path.iterdir())
    real = tmp_path / 'real'
    real.mkdir()
    with pytest.raises(RuntimeError):
        host.replace_link(real, first)


def test_platform_locations():
    assert host.venv_root(host.venv_python(Path('env'))) == Path('env')
    assert host.program(Path('bin/llama-server')).name == ('llama-server.exe' if host.WINDOWS else 'llama-server')
    assert host.program(Path('bin/llama-server.exe')).name == 'llama-server.exe'
    assert host.default_runtime().name == ('Inflect' if host.WINDOWS else 'linux-llm-loader')
