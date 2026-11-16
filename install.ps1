<#
Install or update pcbridge for this Windows user, then hand over to `pcbridge setup`.

    irm https://github.com/Eymistaken/Pcbridge/releases/latest/download/install.ps1 | iex
    & ([scriptblock]::Create((irm https://github.com/Eymistaken/Pcbridge/releases/latest/download/install.ps1))) -Yes
    powershell -ExecutionPolicy Bypass -File install.ps1 [-Yes] [-Version vX.Y.Z | -Wheel FILE] [setup options]
    powershell -ExecutionPolicy Bypass -File install.ps1 -Uninstall [-Purge] [-Yes]

From a release: downloads the Windows wheel, checks its SHA-256 against the
release's sha256sums.txt, installs it into %LOCALAPPDATA%\pcbridge\data\venv,
then runs `pcbridge setup`, which shows the plan and asks once. With -Wheel it
installs a wheel file you have (checked against a sha256sums.txt next to it).

Needs Python 3.12 or newer. Without one it downloads a Python for pcbridge
only with uv (into %LOCALAPPDATA%\pcbridge, no administrator rights, nothing
else on the system changes). Never needs administrator rights: everything is
per user. Run it again to update or repair. Arguments it does not know go to
`pcbridge setup`. With `irm | iex` there are no arguments; set
PCBRIDGE_VERSION to install a given release.
#>
[CmdletBinding()]
param(
    [switch]$Yes,
    [string]$Version = $env:PCBRIDGE_VERSION,
    [string]$Wheel = "",
    [string]$Python = "",
    [switch]$Uninstall,
    [switch]$Purge,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$SetupArgs = @()
)

# This script installs the Windows build. PowerShell 7 also runs on Linux and
# macOS, where $IsWindows is false (Windows PowerShell 5.1, which runs only on
# Windows, does not define it). Stop there before anything changes.
if ($PSVersionTable.PSEdition -eq "Core" -and -not $IsWindows) {
    if ($IsMacOS) {
        Write-Host "pcbridge install: macOS is not supported: pcbridge runs on Linux and Windows." -ForegroundColor Red
    } else {
        Write-Host "pcbridge install: this script installs pcbridge on Windows. On Linux, run this in a terminal instead:" -ForegroundColor Red
        Write-Host ""
        Write-Host "    curl -fsSL https://github.com/Eymistaken/Pcbridge/releases/latest/download/install.sh | sh"
        Write-Host ""
    }
    if ($PSCommandPath) { exit 1 }
    throw "pcbridge install stopped"
}

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Repo = "Eymistaken/Pcbridge"
$Data = Join-Path $env:LOCALAPPDATA "pcbridge\data"
$Venv = Join-Path $Data "venv"
# A uv release at least two weeks old; PCBRIDGE_UV_VERSION overrides it.
$UvVersion = if ($env:PCBRIDGE_UV_VERSION) { $env:PCBRIDGE_UV_VERSION } else { "0.12.21" }
$UvPython = "3.12"
$Temp = Join-Path ([IO.Path]::GetTempPath()) ("pcbridge-install-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force $Temp | Out-Null

function Say([string]$Text) { Write-Host $Text }
function Warn([string]$Text) { Write-Host "pcbridge install: $Text" -ForegroundColor Yellow }
# `exit` would close the window of a user who ran `irm ... | iex`; only a
# script run as a file ends with an exit code ($PSCommandPath is set then).
function Finish([int]$Code) {
    Remove-Item -Recurse -Force $Temp -ErrorAction SilentlyContinue
    if ($PSCommandPath) { exit $Code }
    if ($Code -ne 0) { throw "pcbridge install stopped" }
}
function Die([string]$Text) {
    Write-Host "pcbridge install: $Text" -ForegroundColor Red
    Finish 1
}

function Download([string]$Url, [string]$File) {
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $File -Headers @{ "User-Agent" = "pcbridge-install" }
    } catch {
        Die "download failed: $Url ($($_.Exception.Message))"
    }
}

function Check-Sha256([string]$File, [string]$SumsFile) {
    # A sha256sums.txt line is "<hex>  <name>" (sha256sum's format).
    $name = Split-Path $File -Leaf
    $line = Get-Content $SumsFile | Where-Object { $_ -match ("\s\*?" + [regex]::Escape($name) + "$") } | Select-Object -First 1
    if (-not $line) { return $false }
    $want = ($line -split "\s+")[0].ToLowerInvariant()
    $got = (Get-FileHash -Algorithm SHA256 $File).Hash.ToLowerInvariant()
    if ($got -ne $want) { Die "checksum mismatch: $name" }
    return $true
}

function Python-Ok([string]$Exe) {
    if (-not $Exe) { return $false }
    try {
        & $Exe -c "import sys, venv, ensurepip; sys.exit(sys.version_info < (3, 12))" 2>$null | Out-Null
        return $LASTEXITCODE -eq 0
    } catch { return $false }
}

# -- uninstall ------------------------------------------------------------------

function Recycle([string]$Path) {
    # To the Recycle Bin, never a permanent delete: these are our own files.
    if (-not (Test-Path $Path)) { return }
    Add-Type -AssemblyName Microsoft.VisualBasic
    $item = Get-Item $Path
    if ($item.PSIsContainer) {
        [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($Path, "OnlyErrorDialogs", "SendToRecycleBin")
    } else {
        [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($Path, "OnlyErrorDialogs", "SendToRecycleBin")
    }
    Say "moved to the Recycle Bin: $Path"
}

if ($Uninstall) {
    $pcb = Join-Path $Venv "Scripts\pcbridge.exe"
    if (-not (Test-Path $pcb)) {
        $found = Get-Command pcbridge -ErrorAction SilentlyContinue
        if ($found) { $pcb = $found.Source } else { Die "pcbridge is not installed for this user." }
    }
    $uninstallArgs = @("uninstall")
    if ($Yes) { $uninstallArgs += "--yes" }
    if ($Purge) { $uninstallArgs += "--purge" }
    & $pcb @uninstallArgs
    # The venv cannot remove itself while it runs; it goes now.
    foreach ($dir in @($Venv, (Join-Path $Data "python"), (Join-Path $Data "uv"))) { Recycle $dir }
    if ($Purge) {
        # `pcbridge uninstall --purge` cannot move the data folder while its
        # own venv inside it runs, and the cache, the runtime folder and
        # idle.json are not its to purge. All of them are under one root,
        # which goes now.
        Recycle (Join-Path $env:LOCALAPPDATA "pcbridge")
    }
    Finish 0
    return
}

# -- Python ----------------------------------------------------------------------

function Find-Python {
    if ($Python) {
        if (Python-Ok $Python) { return $Python }
        Die "$Python is not Python 3.12 or newer with venv"
    }
    # The py launcher knows every installed Python; python.exe on PATH may be
    # the Microsoft Store stub, which Python-Ok rejects (it does not run code).
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        foreach ($v in @("3.14", "3.13", "3.12")) {
            try {
                $exe = (& $py.Source "-$v" -c "import sys; print(sys.executable)" 2>$null)
                if ($LASTEXITCODE -eq 0 -and (Python-Ok $exe)) { return $exe }
            } catch { }
        }
    }
    foreach ($name in @("python", "python3")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and (Python-Ok $cmd.Source)) { return $cmd.Source }
    }
    $own = Get-ChildItem (Join-Path $Data "python") -Filter python.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($own -and (Python-Ok $own.FullName)) { return $own.FullName }
    return $null
}

function Uv-Python {
    $zip = "uv-x86_64-pc-windows-msvc.zip"
    $base = "https://github.com/astral-sh/uv/releases/download/$UvVersion"
    Say "Downloading uv $UvVersion (to fetch Python $UvPython for pcbridge only)..."
    Download "$base/$zip" (Join-Path $Temp $zip)
    Download "$base/$zip.sha256" (Join-Path $Temp "uv.sha256")
    $want = ((Get-Content (Join-Path $Temp "uv.sha256") -Raw) -split "\s+")[0].ToLowerInvariant()
    $got = (Get-FileHash -Algorithm SHA256 (Join-Path $Temp $zip)).Hash.ToLowerInvariant()
    if ($got -ne $want) { Die "checksum mismatch: $zip" }
    Expand-Archive -Force (Join-Path $Temp $zip) (Join-Path $Temp "uv")
    New-Item -ItemType Directory -Force (Join-Path $Data "uv") | Out-Null
    $uv = Join-Path $Data "uv\uv.exe"
    Copy-Item -Force (Get-ChildItem (Join-Path $Temp "uv") -Filter uv.exe -Recurse | Select-Object -First 1).FullName $uv
    # Everything stays under $Data: no Python on PATH, nothing for the system.
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $Data "python"
    $env:UV_CACHE_DIR = Join-Path $Data "uv\cache"
    $env:UV_NO_CONFIG = "1"
    & $uv python install --no-bin $UvPython
    if ($LASTEXITCODE -ne 0) { Die "uv could not install Python $UvPython" }
    $exe = (& $uv python find --managed-python --no-config $UvPython)
    Remove-Item -Recurse -Force $env:UV_CACHE_DIR -ErrorAction SilentlyContinue
    if (-not (Python-Ok $exe)) { Die "the downloaded Python does not run: $exe" }
    return $exe
}

$Py = Find-Python
if (-not $Py) {
    Say "pcbridge needs Python 3.12 or newer, and none was found."
    $answer = "y"
    if (-not $Yes) {
        $answer = Read-Host "Download a Python for pcbridge only, with uv, into $Data? [Y/n]"
        if (-not $answer) { $answer = "y" }
    }
    if ($answer -notmatch "^[Yy]") { Say "Nothing was changed. Install Python 3.12 or newer, then run this again."; Finish 1; return }
    $Py = Uv-Python
}
Say ("Python: $Py (" + (& $Py -c "import platform; print(platform.python_version())") + ")")

# -- the wheel ---------------------------------------------------------------------

$Constraints = ""
if ($Wheel) {
    if (-not (Test-Path $Wheel)) { Die "no such wheel: $Wheel" }
    $Wheel = (Resolve-Path $Wheel).Path
    $dir = Split-Path $Wheel -Parent
    $sums = Join-Path $dir "sha256sums.txt"
    if (Test-Path $sums) {
        if (Check-Sha256 $Wheel $sums) { Say "sha256 ok: $(Split-Path $Wheel -Leaf)" }
        else { Say "note: sha256sums.txt next to the wheel does not list it; its checksum is not checked" }
    } else {
        Say "note: no sha256sums.txt next to the wheel; its checksum is not checked"
    }
    if (Test-Path (Join-Path $dir "constraints.txt")) { $Constraints = Join-Path $dir "constraints.txt" }
} else {
    if ($env:PROCESSOR_ARCHITECTURE -ne "AMD64") {
        Die "there is no release wheel for $($env:PROCESSOR_ARCHITECTURE) yet"
    }
    if (-not $Version) {
        try {
            $latest = Invoke-RestMethod -UseBasicParsing -Uri "https://api.github.com/repos/$Repo/releases/latest" -Headers @{ "User-Agent" = "pcbridge-install" }
            $Version = $latest.tag_name
        } catch { Die "could not read the latest release from GitHub" }
    }
    $base = "https://github.com/$Repo/releases/download/$Version"
    Say "Downloading pcbridge $Version"
    $sums = Join-Path $Temp "sha256sums.txt"
    Download "$base/sha256sums.txt" $sums
    $line = Get-Content $sums | Where-Object { $_ -match "pcbridge-[0-9][^ ]*-py3-none-win_amd64\.whl$" } | Select-Object -First 1
    if (-not $line) { Die "release $Version has no Windows wheel" }
    $name = (($line -split "\s+")[1]).TrimStart("*")
    $Wheel = Join-Path $Temp $name
    Download "$base/$name" $Wheel
    [void](Check-Sha256 $Wheel $sums)
    Say "sha256 ok: $name"
    if (Get-Content $sums | Where-Object { $_ -match "\s\*?constraints\.txt$" }) {
        $Constraints = Join-Path $Temp "constraints.txt"
        Download "$base/constraints.txt" $Constraints
        [void](Check-Sha256 $Constraints $sums)
    } else {
        Say "note: release $Version has no constraints.txt; the newest compatible dependencies are used"
    }
}

# -- pcbridge into a venv ----------------------------------------------------------------

New-Item -ItemType Directory -Force $Data | Out-Null
# Kept for `pcbridge setup`, which may add the desktop extra later.
$cArgs = @()
if ($Constraints) {
    Copy-Item -Force $Constraints (Join-Path $Data "constraints.txt")
    $cArgs = @("-c", (Join-Path $Data "constraints.txt"))
}
$VenvPython = Join-Path $Venv "Scripts\python.exe"
if ((Test-Path $VenvPython) -and -not (Python-Ok $VenvPython)) {
    Say "The venv at $Venv has no working Python 3.12+ any more; making it again."
    Recycle $Venv
}
if (-not (Test-Path $VenvPython)) {
    & $Py -m venv $Venv
    if ($LASTEXITCODE -ne 0) { Die "could not create the venv at $Venv" }
}
# A running pcbridge holds its files open; stop it before pip replaces them.
$running = Join-Path $Venv "Scripts\pcbridge.exe"
if (Test-Path $running) { & $running stop --keep-jobs 2>$null | Out-Null }
Say "Installing into $Venv (pip takes a minute)..."
& $VenvPython -m pip install -q --disable-pip-version-check --upgrade pip
& $VenvPython -m pip install -q --disable-pip-version-check @cArgs --force-reinstall --no-deps $Wheel
if ($LASTEXITCODE -ne 0) { Die "pip could not install $Wheel" }
& $VenvPython -m pip install -q --disable-pip-version-check @cArgs "$Wheel[desktop]"
if ($LASTEXITCODE -ne 0) {
    & $VenvPython -m pip install -q --disable-pip-version-check @cArgs $Wheel
    if ($LASTEXITCODE -ne 0) { Die "pip could not install pcbridge's dependencies" }
    Warn "the desktop extra (Pillow) was not installed; only desktop control uses it, and pcbridge setup tries again."
}

Say ""
$setup = @("setup")
if ($Yes) { $setup += "--yes" }
$setup += $SetupArgs
& (Join-Path $Venv "Scripts\pcbridge.exe") @setup
Finish $LASTEXITCODE
