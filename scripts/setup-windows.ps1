<#
  Install Inflect on 64-bit Windows 10/11 with an NVIDIA GPU, without administrator
  rights and without changing the GPU driver. Windows counterpart of setup-ubuntu.sh.
#>
[CmdletBinding()]
param(
    [string]$ModelDir = $env:INFLECT_MODEL_ROOT,
    [string]$RuntimeDir = $(if ($env:INFLECT_RUNTIME) { $env:INFLECT_RUNTIME } else { Join-Path $env:LOCALAPPDATA 'Inflect' }),
    [switch]$NoShortcut,
    [switch]$PreflightOnly,
    [switch]$WithLlama,
    [switch]$SkipGpuCheck,
    [string]$ImportLinuxState,
    [switch]$Help
)

$ErrorActionPreference = 'Stop'
$ProjectDir = Split-Path -Parent $PSScriptRoot

function Show-Usage {
    @'
Usage: powershell -ExecutionPolicy Bypass -File .\scripts\setup-windows.ps1 -ModelDir PATH [options]

Required:
  -ModelDir PATH       Folder containing downloaded model folders/files

Optional:
  -RuntimeDir PATH     Private Python and engine files (default: %LOCALAPPDATA%\Inflect)
  -NoShortcut          Configure Inflect without a Start menu shortcut
  -PreflightOnly       Check the machine and arguments without installing
  -WithLlama           Install the pinned CUDA llama.cpp runtime for GGUF and embedded MTP
  -SkipGpuCheck        Install everything but defer the CUDA smoke tests (rerun later without it)
  -ImportLinuxState PATH
                       Import profiles, library choices, benchmark history and chats from a
                       copied Linux state folder (see scripts\import-linux-state.py)
  -Help                Show this help
'@
}

function Fail([string]$Message) {
    Write-Host $Message -ForegroundColor Red
    exit 1
}

# Windows PowerShell 5.1 turns native stderr into errors under 'Stop'; judge by exit code only.
# Plain functions ($args), so native flags such as -c or -v are never bound as PowerShell parameters.
function Invoke-Native {
    $file = $args[0]
    $arguments = @($args | Select-Object -Skip 1)  # Always an array: splatting a lone string passes characters.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $file @arguments 2>&1 | ForEach-Object { Write-Host "$_" }
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($code -ne 0) { throw "$file $($arguments -join ' ') failed with exit code $code." }
}

function Test-Native {
    $file = $args[0]
    $arguments = @($args | Select-Object -Skip 1)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $file @arguments *> $null
        return ($LASTEXITCODE -eq 0)
    } finally {
        $ErrorActionPreference = $previous
    }
}

if ($Help) { Show-Usage; exit 0 }
if (-not $ModelDir) { Write-Host 'Missing -ModelDir PATH.' -ForegroundColor Red; Show-Usage; exit 2 }
if (-not (Test-Path -LiteralPath $ModelDir -PathType Container)) { Fail "Model directory does not exist: $ModelDir" }
$ModelDir = (Resolve-Path -LiteralPath $ModelDir).Path
if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') {
    Fail 'This pinned runtime currently supports 64-bit x86 Windows only.'
}

$missing = @()
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { $missing += 'Git for Windows (winget install --exact --id Git.Git)' }
if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
    $missing += 'NVIDIA driver with nvidia-smi (install the current GeForce/Studio or RTX driver, then reboot)'
}
if ($missing.Count) { Fail ("Install these prerequisites first:`n  " + ($missing -join "`n  ")) }

$gpu = (& nvidia-smi --query-gpu=name,driver_version,memory.total,compute_cap --format=csv,noheader | Select-Object -First 1)
Write-Host $gpu
$driverMajor = 0
[void][int]::TryParse(($gpu -split ',')[1].Trim().Split('.')[0], [ref]$driverMajor)
if ($driverMajor -lt 570) {
    Fail "The pinned CUDA 12.8 runtime requires an NVIDIA 570-series or newer driver; found $(($gpu -split ',')[1].Trim())."
}

$runtimeParent = Split-Path -Parent $RuntimeDir
New-Item -ItemType Directory -Force -Path $runtimeParent | Out-Null
$drive = Get-PSDrive -Name ((Resolve-Path -LiteralPath $runtimeParent).Path.Substring(0, 1))
$needed = 12GB
if ($WithLlama) { $needed += 4GB }
if ($drive.Free -lt $needed) { Fail "The runtime drive needs at least $([math]::Round($needed / 1GB)) GiB free." }
if (-not (Test-Path -LiteralPath (Join-Path $ProjectDir 'frontend\dist\index.html'))) { Fail 'The compiled interface is missing from this checkout.' }
if ($ImportLinuxState -and -not (Test-Path -LiteralPath (Join-Path $ImportLinuxState 'profiles.json'))) {
    Fail "No Linux Inflect state (profiles.json) found in: $ImportLinuxState"
}
if ($PreflightOnly) {
    Write-Host 'Preflight passed. The GPU driver, model directory, Git, and runtime disk are available.'
    exit 0
}

New-Item -ItemType Directory -Force -Path (Join-Path $RuntimeDir 'bin'), (Join-Path $RuntimeDir 'sources'), (Join-Path $ProjectDir '.runtime\wheels') | Out-Null
$env:UV_CACHE_DIR = Join-Path $RuntimeDir 'cache\uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $RuntimeDir 'python'
$env:GIT_TERMINAL_PROMPT = '0'

$uv = Join-Path $RuntimeDir 'bin\uv.exe'
if (-not (Test-Path -LiteralPath $uv)) {
    $systemUv = Get-Command uv -ErrorAction SilentlyContinue
    if ($systemUv) {
        $uv = $systemUv.Source
    } else {
        $env:UV_INSTALL_DIR = Join-Path $RuntimeDir 'bin'
        $env:UV_NO_MODIFY_PATH = '1'
        Invoke-Native powershell -NoProfile -ExecutionPolicy Bypass -Command 'irm https://astral.sh/uv/install.ps1 | iex'
    }
}

# uv can report a failed minor-version link although the interpreter installed; trust 'find'.
$previous = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
& $uv python install 3.12 2>&1 | ForEach-Object { Write-Host "$_" }
$ErrorActionPreference = $previous
if (-not (Test-Native $uv python find 3.12)) { Fail 'Python 3.12 could not be installed by uv.' }

$managerDir = Join-Path $RuntimeDir 'manager'
$managerPython = Join-Path $managerDir 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $managerPython)) { Invoke-Native $uv venv --python 3.12 --seed $managerDir }
Invoke-Native $uv pip install --python $managerPython -r (Join-Path $ProjectDir 'requirements-app.txt')

$lock = Get-Content -Raw -LiteralPath (Join-Path $ProjectDir 'runtime-locks\exllama.json') | ConvertFrom-Json
$pinned = $lock.platforms.windows
# Windows may pin its own ExLlamaV3 build (see "reason" in the lock); shared fields apply otherwise.
$exlPackage = if ($pinned.package_version) { $pinned.package_version } else { $lock.package_version }
$tabbyDir = Join-Path $RuntimeDir 'sources\tabbyAPI'
if (-not (Test-Path -LiteralPath (Join-Path $tabbyDir '.git'))) {
    # Keep upstream LF endings regardless of core.autocrlf: the patches expect them.
    Invoke-Native git -c core.autocrlf=false clone https://github.com/theroyallab/tabbyAPI.git $tabbyDir
}
Invoke-Native git -C $tabbyDir config core.autocrlf false
Invoke-Native git -C $tabbyDir fetch --tags origin
Invoke-Native git -C $tabbyDir checkout --detach $lock.tabby_revision
foreach ($patch in 'tabby-exl3-model-card.patch', 'tabby-qwen-tool-schema.patch') {
    $patchFile = Join-Path $ProjectDir "patches\$patch"
    if (-not (Test-Native git -C $tabbyDir apply --reverse --check $patchFile)) { Invoke-Native git -C $tabbyDir apply $patchFile }
}

$exlDir = Join-Path $RuntimeDir 'exl3'
$exlPython = Join-Path $exlDir 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $exlPython)) { Invoke-Native $uv venv --python 3.12 --seed $exlDir }
Invoke-Native $managerPython (Join-Path $ProjectDir 'scripts\fetch_exl_wheel.py')
Invoke-Native $uv pip install --python $exlPython -r (Join-Path $ProjectDir $pinned.dependencies) `
    "torch @ $($pinned.torch_wheel)" (Join-Path $ProjectDir ".runtime\wheels\$($pinned.wheel)") $tabbyDir

# Fixes to the installed Python modules that the pinned wheel still needs (no CUDA rebuild).
$exlSite = (& $exlPython -c 'import sysconfig; print(sysconfig.get_path(''purelib''))').Trim()
foreach ($sitePatch in @($pinned.site_patches)) {
    if (-not $sitePatch) { continue }
    $patchFile = Join-Path $ProjectDir $sitePatch
    if (-not (Test-Native git -C $exlSite apply --reverse --check $patchFile)) { Invoke-Native git -C $exlSite apply $patchFile }
}

# Package identity without touching the GPU: importing torch does not initialise CUDA.
Invoke-Native $exlPython -c "import importlib.metadata as m, torch; assert m.version('exllamav3') == '$exlPackage', m.version('exllamav3'); assert torch.__version__ == '$($lock.torch)', torch.__version__; print('ExLlamaV3', m.version('exllamav3'), '/ torch', torch.__version__)"
Invoke-Native $uv pip check --python $exlPython
if ($SkipGpuCheck) {
    Write-Host 'GPU smoke test deferred (-SkipGpuCheck). Run setup again without it to verify CUDA.' -ForegroundColor Yellow
} else {
    $env:INFLECT_RUNTIME = $RuntimeDir
    Invoke-Native $exlPython (Join-Path $ProjectDir 'scripts\probe_runtime.py')
}

if ($WithLlama) {
    $llamaArgs = @((Join-Path $ProjectDir 'scripts\install-llama.py'), '--runtime-dir', $RuntimeDir)
    if ($SkipGpuCheck) { $llamaArgs += '--no-gpu-check' }
    Invoke-Native $managerPython @llamaArgs
}

$env:INFLECT_PROJECT = $ProjectDir
$env:INFLECT_RUNTIME = $RuntimeDir
$desktopArgs = @((Join-Path $ProjectDir 'scripts\install-desktop.py'), '--model-dir', $ModelDir)
if ($NoShortcut) { $desktopArgs += '--no-desktop' }
Invoke-Native $managerPython @desktopArgs
if ($ImportLinuxState) {
    Invoke-Native $managerPython (Join-Path $ProjectDir 'scripts\import-linux-state.py') --from $ImportLinuxState
}

Write-Host ''
Write-Host 'Setup complete.' -ForegroundColor Green
Write-Host "Models:  $ModelDir"
Write-Host "Runtime: $RuntimeDir"
if ($NoShortcut) {
    Write-Host 'Run launch.cmd to start Inflect.'
} else {
    Write-Host 'Open Inflect from the Start menu, or run launch.cmd.'
}
