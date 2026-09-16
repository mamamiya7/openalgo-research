# A private, pinned toolchain: no administrator rights, PATH or permanent policy changes.
[CmdletBinding()]
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$SetupArgs)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$runtimePath = Join-Path $taskRoot '.research-runtime'
$uvPath = Join-Path $runtimePath 'bin\uv.exe'
$downloadDirectory = $null
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
        if ((Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) {
            throw 'The installer download failed its integrity check. Nothing was installed. Check your connection and retry Setup.cmd.'
        }
        Expand-Archive -LiteralPath $archivePath -DestinationPath (Join-Path $downloadDirectory 'unpacked')
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
    Write-Host '[2/6] Preparing Python 3.12 (first setup needs an internet connection)...'
    & $uvPath --directory $taskRoot run --no-project --managed-python --python 3.12 python (Join-Path $PSScriptRoot 'research_setup.py') --uv $uvPath @SetupArgs
    if ($LASTEXITCODE -ne 0) { throw 'Setup stopped. Your existing settings and saved results have not been replaced. See the message above; retry Setup.cmd after correcting it.' }
} catch {
    Write-Host ''
    Write-Host ('OpenAlgo Research: ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host 'Downloads require access to github.com, astral-sh.github.io and pypi.org. No Python or Node installation is needed for a release ZIP.'
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
