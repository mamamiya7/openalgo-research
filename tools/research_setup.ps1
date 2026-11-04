# A private, pinned toolchain: no administrator rights, PATH or permanent policy changes.
[CmdletBinding()]
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$SetupArgs)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$runtimePath = Join-Path $taskRoot '.research-runtime'
$uvPath = Join-Path $runtimePath 'bin\uv.exe'
$downloadDirectory = $null

function Get-SignedPython312 {
    # Inspect known installer locations before executing any candidate. This is
    # runtime selection, never an Application Control or execution-policy change.
    $taskCandidates = [Collections.Generic.List[string]]::new()
    if ($env:OPENALGO_PYTHON312) { $taskCandidates.Add($env:OPENALGO_PYTHON312) }
    foreach ($taskRegistryRoot in @('HKCU:\Software\Python\PythonCore', 'HKLM:\Software\Python\PythonCore', 'HKLM:\Software\WOW6432Node\Python\PythonCore')) {
        foreach ($taskPythonTag in @('3.12', '3.12-64')) {
            $taskRegistryPath = Join-Path $taskRegistryRoot ($taskPythonTag + '\InstallPath')
            if (Test-Path -LiteralPath $taskRegistryPath) {
                $taskRegistryKey = Get-Item -LiteralPath $taskRegistryPath
                $taskExecutable = $taskRegistryKey.GetValue('ExecutablePath')
                $taskInstallPath = $taskRegistryKey.GetValue('')
                if ($taskExecutable) { $taskCandidates.Add([string]$taskExecutable) }
                if ($taskInstallPath) { $taskCandidates.Add((Join-Path $taskInstallPath 'python.exe')) }
            }
        }
    }
    if ($env:LOCALAPPDATA) { $taskCandidates.Add((Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe')) }
    if ($env:ProgramFiles) { $taskCandidates.Add((Join-Path $env:ProgramFiles 'Python312\python.exe')) }
    foreach ($taskCandidate in ($taskCandidates | Select-Object -Unique)) {
        try {
            if (-not (Test-Path -LiteralPath $taskCandidate -PathType Leaf)) { continue }
            $taskSignature = Microsoft.PowerShell.Security\Get-AuthenticodeSignature -LiteralPath $taskCandidate
            if ($taskSignature.Status -ne 'Valid' -or $null -eq $taskSignature.SignerCertificate -or
                $taskSignature.SignerCertificate.Subject -notmatch '(?:^|,)\s*(?:CN|O)=Python Software Foundation(?:,|$)') { continue }
            $taskVersion = & $taskCandidate -I -S -c 'import sys; print(sys.implementation.name, chr(46).join(map(str, sys.version_info[:2])), 64 if sys.maxsize > 2**32 else 32)' 2>$null
            if ($LASTEXITCODE -eq 0 -and $taskVersion -eq 'cpython 3.12 64') {
                return [IO.Path]::GetFullPath($taskCandidate)
            }
        } catch {
            # An inaccessible, invalid or blocked candidate is not executable
            # authority. Keep discovery bounded to the explicit known locations.
            continue
        }
    }
    return $null
}

try {
    if (-not (Test-Path -LiteralPath (Join-Path $taskRoot 'frontend\dist\index.html'))) {
        throw 'The built interface is missing. Download the openalgo-research ZIP from GitHub Releases, not the Source code ZIP. Developers: run npm ci and npm run build inside frontend first.'
    }
    if (-not (Test-Path -LiteralPath $uvPath)) {
        $taskArchitecture = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
        switch ($taskArchitecture) {
            'AMD64' { $asset = 'uv-x86_64-pc-windows-msvc.zip'; $expectedHash = '4c4d49d8738847d9b71ba319e49a5688c93eac0fe6204b1df24e98528dddf39a' }
            default { throw 'This release supports x64 Windows. Native ARM64 and 32-bit installations are not supported by the locked research dependencies.' }
        }
        Write-Host '[1/6] Downloading the private installer (uv 0.12.5)...'
        New-Item -ItemType Directory -Path $runtimePath -Force | Out-Null
        $downloadDirectory = Join-Path $runtimePath ('download-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $downloadDirectory | Out-Null
        $archivePath = Join-Path $downloadDirectory $asset
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/astral-sh/uv/releases/download/0.12.5/$asset" -OutFile $archivePath -TimeoutSec 240
        # The optional hash/archive script modules may be unavailable when
        # Windows PowerShell inherits another host's module path. Use .NET's
        # built-in implementations without relaxing checksum verification.
        $archiveStream = $null
        $hashAlgorithm = [Security.Cryptography.SHA256]::Create()
        try {
            $archiveStream = [IO.File]::OpenRead($archivePath)
            $actualHash = [BitConverter]::ToString($hashAlgorithm.ComputeHash($archiveStream)).Replace('-', '').ToLowerInvariant()
        } finally {
            if ($archiveStream) { $archiveStream.Dispose() }
            $hashAlgorithm.Dispose()
        }
        if ($actualHash -ne $expectedHash) {
            throw 'The installer download failed its integrity check. Nothing was installed. Check your connection and retry Setup.cmd.'
        }
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [IO.Compression.ZipFile]::ExtractToDirectory($archivePath, (Join-Path $downloadDirectory 'unpacked'))
        $downloadedUv = Get-ChildItem -LiteralPath (Join-Path $downloadDirectory 'unpacked') -Filter uv.exe -Recurse | Select-Object -First 1
        if (-not $downloadedUv) { throw 'The verified download does not contain uv.exe. Download the release again.' }
        New-Item -ItemType Directory -Path (Split-Path -Parent $uvPath) -Force | Out-Null
        Copy-Item -LiteralPath $downloadedUv.FullName -Destination $uvPath
    }
    $uvVersion = & $uvPath --version
    if ($LASTEXITCODE -ne 0 -or $uvVersion -notmatch '^uv 0\.12\.5(?:\s|$)') {
        throw 'The private installer is not uv 0.12.5. Remove .research-runtime\bin\uv.exe and run Setup.cmd again.'
    }
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $runtimePath 'python'
    $env:UV_CACHE_DIR = Join-Path $runtimePath 'cache'
    # uv rejects a preference together with --managed-python, including when
    # the preference arrives from an inherited environment variable.
    $env:UV_PYTHON_PREFERENCE = $null
    $env:UV_MANAGED_PYTHON = $null
    $env:UV_NO_MANAGED_PYTHON = $null
    $env:UV_NO_PROGRESS = '1'
    $taskSignedPython = Get-SignedPython312
    if ($taskSignedPython) {
        Write-Host '[2/6] Using your signed Python 3.12 installation...'
        & $taskSignedPython (Join-Path $PSScriptRoot 'research_setup.py') --uv $uvPath @SetupArgs
    } else {
        Write-Host '[2/6] Preparing private Python 3.12 (first setup needs an internet connection)...'
        & $uvPath --directory $taskRoot run --no-project --managed-python --python 3.12 python (Join-Path $PSScriptRoot 'research_setup.py') --uv $uvPath @SetupArgs
    }
    if ($LASTEXITCODE -ne 0) { throw 'Setup stopped. Your existing settings and saved results have not been replaced. See the message above; retry Setup.cmd after correcting it.' }
} catch {
    Write-Host ''
    Write-Host ('OpenAlgo Research: ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host 'Downloads require access to github.com, astral-sh.github.io and pypi.org. No Python or Node installation is needed for a release ZIP.'
    Write-Host 'If Windows blocks the private Python runtime, install signed 64-bit Python 3.12 from python.org and run Setup.cmd again. Existing settings and results are retained.'
    exit 1
} finally {
    if ($downloadDirectory -and (Test-Path -LiteralPath $downloadDirectory)) {
        $checkedRuntime = [IO.Path]::GetFullPath($runtimePath).TrimEnd('\') + '\'
        $checkedDownload = [IO.Path]::GetFullPath($downloadDirectory)
        if ($checkedDownload.StartsWith($checkedRuntime, [StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $checkedDownload -Recurse -Force
        }
    }
}
