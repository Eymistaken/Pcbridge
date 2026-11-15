# Make the pcbridge Windows 11 test VM quiet: as little telemetry, advertising,
# suggested apps and background download as Windows 11 Pro allows.
# Run by setup.ps1 after OpenSSH is installed, as the logged-on test user
# (elevated), so HKCU is the test user's hive.
#
# Kept on purpose, because tests use them or real users have them: Notepad,
# Paint, Calculator, Terminal, Edge, Microsoft Defender, Windows Update itself.
# Every step tolerates failure: a missing app or key must not stop the setup.
$ErrorActionPreference = 'Continue'

function Set-Reg([string]$Path, [string]$Name, $Value, [string]$Type = 'DWord') {
    try {
        if (-not (Test-Path $Path)) { New-Item -Path $Path -Force | Out-Null }
        Set-ItemProperty -Path $Path -Name $Name -Value $Value -Type $Type -ErrorAction Stop
    } catch { Write-Output "skip $Path\$Name : $_" }
}

# Diagnostic data: 0 is honored only by Enterprise and Education; Pro treats it
# as 1 ("Required"), the lowest it has.
Set-Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DataCollection' AllowTelemetry 0
Set-Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DataCollection' DoNotShowFeedbackNotifications 1
Set-Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DataCollection' DisableOneSettingsDownloads 1
Set-Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DataCollection' LimitDiagnosticLogCollection 1
Set-Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DataCollection' LimitDumpCollection 1
foreach ($svc in 'DiagTrack', 'dmwappushservice') {
    Stop-Service -Name $svc -Force -ErrorAction SilentlyContinue
    Set-Service -Name $svc -StartupType Disabled -ErrorAction SilentlyContinue
}
foreach ($task in @(
        '\Microsoft\Windows\Application Experience\Microsoft Compatibility Appraiser',
        '\Microsoft\Windows\Application Experience\ProgramDataUpdater',
        '\Microsoft\Windows\Customer Experience Improvement Program\Consolidator',
        '\Microsoft\Windows\Customer Experience Improvement Program\UsbCeip',
        '\Microsoft\Windows\Feedback\Siuf\DmClient',
        '\Microsoft\Windows\Windows Error Reporting\QueueReporting')) {
    $folder = $task.Substring(0, $task.LastIndexOf('\') + 1)
    $name = $task.Substring($task.LastIndexOf('\') + 1)
    Disable-ScheduledTask -TaskPath $folder -TaskName $name -ErrorAction SilentlyContinue | Out-Null
}
Set-Reg 'HKLM:\SOFTWARE\Policies\Microsoft\SQMClient\Windows' CEIPEnable 0
Set-Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Windows Error Reporting' Disabled 1
Set-Reg 'HKCU:\Software\Microsoft\Siuf\Rules' NumberOfSIUFInPeriod 0

# Advertising, tailored experiences, suggested and silently installed apps.
Set-Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\AdvertisingInfo' DisabledByGroupPolicy 1
Set-Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\CloudContent' DisableWindowsConsumerFeatures 1
Set-Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\CloudContent' DisableSoftLanding 1
Set-Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\CloudContent' DisableCloudOptimizedContent 1
Set-Reg 'HKCU:\Software\Policies\Microsoft\Windows\CloudContent' DisableTailoredExperiencesWithDiagnosticData 1
$cdm = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager'
foreach ($name in 'ContentDeliveryAllowed', 'OemPreInstalledAppsEnabled', 'PreInstalledAppsEnabled',
        'SilentInstalledAppsEnabled', 'SoftLandingEnabled', 'SystemPaneSuggestionsEnabled',
        'SubscribedContent-338388Enabled', 'SubscribedContent-338389Enabled',
        'SubscribedContent-338393Enabled', 'SubscribedContent-353694Enabled',
        'SubscribedContent-353696Enabled', 'SubscribedContent-310093Enabled') {
    Set-Reg $cdm $name 0
}
Set-Reg 'HKCU:\Software\Microsoft\Windows\CurrentVersion\UserProfileEngagement' ScoobeSystemSettingEnabled 0

# Web results in Start search, Copilot, Recall, Widgets, activity history.
Set-Reg 'HKCU:\Software\Policies\Microsoft\Windows\Explorer' DisableSearchBoxSuggestions 1
Set-Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Windows Search' AllowCortana 0
Set-Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Windows Search' ConnectedSearchUseWeb 0
Set-Reg 'HKCU:\Software\Policies\Microsoft\Windows\WindowsCopilot' TurnOffWindowsCopilot 1
Set-Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsAI' DisableAIDataAnalysis 1
Set-Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Dsh' AllowNewsAndInterests 0
Set-Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\System' PublishUserActivities 0
Set-Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\System' EnableActivityFeed 0

# OneDrive stays out of the way; nothing is synced from a test VM.
Set-Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\OneDrive' DisableFileSyncNGSC 1

# Windows Update: no peer-to-peer distribution, no automatic download, and no
# restart while the test user is logged on (a restart mid-test looks like a
# pcbridge failure). Updates are still possible by hand.
Set-Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DeliveryOptimization' DODownloadMode 0
Set-Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU' AUOptions 2
Set-Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU' NoAutoRebootWithLoggedOnUsers 1

# Consumer apps nobody tests with. Removed for the test user and for new users.
$apps = @(
    'Clipchamp.Clipchamp', 'Microsoft.549981C3F5F10', 'Microsoft.BingNews', 'Microsoft.BingSearch',
    'Microsoft.BingWeather', 'Microsoft.Copilot', 'Microsoft.GamingApp', 'Microsoft.GetHelp',
    'Microsoft.Getstarted', 'Microsoft.MicrosoftOfficeHub', 'Microsoft.MicrosoftSolitaireCollection',
    'Microsoft.OutlookForWindows', 'Microsoft.People', 'Microsoft.PowerAutomateDesktop',
    'Microsoft.Todos', 'Microsoft.Windows.DevHome', 'Microsoft.WindowsFeedbackHub',
    'Microsoft.WindowsMaps', 'Microsoft.Xbox.TCUI', 'Microsoft.XboxApp', 'Microsoft.XboxGameOverlay',
    'Microsoft.XboxGamingOverlay', 'Microsoft.XboxIdentityProvider', 'Microsoft.XboxSpeechToTextOverlay',
    'Microsoft.YourPhone', 'Microsoft.ZuneMusic', 'Microsoft.ZuneVideo', 'MSTeams',
    'MicrosoftCorporationII.MicrosoftFamily', 'Microsoft.Edge.GameAssist')
foreach ($app in $apps) {
    Get-AppxPackage -AllUsers -Name $app -ErrorAction SilentlyContinue |
        Remove-AppxPackage -AllUsers -ErrorAction SilentlyContinue
    Get-AppxProvisionedPackage -Online -ErrorAction SilentlyContinue |
        Where-Object DisplayName -eq $app |
        Remove-AppxProvisionedPackage -Online -ErrorAction SilentlyContinue | Out-Null
}
Write-Output 'quiet.ps1 done'
