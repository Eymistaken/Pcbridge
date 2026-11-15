# First-logon setup of the pcbridge Windows 11 test VM (scripts/dev/windows-vm.sh).
# Runs once from the seed ISO through autounattend.xml's FirstLogonCommands.
# Log: C:\pcbridge-setup.log; C:\pcbridge-setup.done marks success.
$ErrorActionPreference = 'Stop'
Start-Transcript -Path 'C:\pcbridge-setup.log' -Append
$src = Split-Path -Parent $MyInvocation.MyCommand.Path

# The test user's session must never sleep, blank or lock on its own:
# a lock in the middle of an input test would look like a pcbridge failure.
powercfg /change monitor-timeout-ac 0
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
Set-ItemProperty -Path 'HKCU:\Control Panel\Desktop' -Name ScreenSaveActive -Value 0
New-Item -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Personalization' -Force | Out-Null
Set-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Personalization' -Name NoLockScreen -Value 1 -Type DWord

# OpenSSH server: a Feature on Demand, downloaded from Windows Update.
Add-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0' | Out-Null
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
if (-not (Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' `
        -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null
}
# The capability's rule allows only the Private profile, and QEMU's user
# network comes up as Public: without this, SSH times out in the banner
# exchange (measured 2026-11-15). The VM is reachable only through QEMU's
# forward on the host's 127.0.0.1.
Set-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -Profile Any
Get-NetConnectionProfile | Set-NetConnectionProfile -NetworkCategory Private

# tester is an administrator, so sshd reads this file, not ~\.ssh\authorized_keys.
# It must be writable only by Administrators (S-1-5-32-544) and SYSTEM (S-1-5-18).
$keys = 'C:\ProgramData\ssh\administrators_authorized_keys'
Copy-Item -Path (Join-Path $src 'id_ed25519.pub') -Destination $keys -Force
icacls.exe $keys /inheritance:r /grant '*S-1-5-32-544:F' /grant '*S-1-5-18:F' | Out-Null

# Telemetry, advertising and consumer apps off, after OpenSSH, because the
# OpenSSH download needs Windows Update as it is. Failures there are logged
# and do not stop the setup.
try { & (Join-Path $src 'quiet.ps1') } catch { Write-Output "quiet.ps1 failed: $_" }

# Setup sets the specialize pass's ComputerName, but OOBE's ComputerName
# plugin replaced it with "W-" on build 26300 (measured 2026-11-15).
# The new name applies at the next restart.
if ($env:COMPUTERNAME -ne 'PCBRIDGE-WIN') {
    try { Rename-Computer -NewName 'PCBRIDGE-WIN' -Force } catch { Write-Output "rename failed: $_" }
}

New-Item -Path 'C:\pcbridge-setup.done' -ItemType File -Force | Out-Null
Stop-Transcript
