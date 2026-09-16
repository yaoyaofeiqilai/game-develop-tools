[CmdletBinding()]
param(
    [string]$HostName = $env:DP_GAME_SSH_HOST,
    [string]$SshUser = $env:DP_GAME_SSH_USER,
    [int]$Port = $(if ($env:DP_GAME_SSH_PORT) { [int]$env:DP_GAME_SSH_PORT } else { 22 }),
    [string]$KeyPath = $env:DP_GAME_SSH_KEY,
    [string]$KnownHosts = $(if ($env:DP_GAME_KNOWN_HOSTS) { $env:DP_GAME_KNOWN_HOSTS } else { Join-Path $HOME ".ssh\known_hosts" })
)

$ErrorActionPreference = "Stop"
$sshExe = Join-Path $env:WINDIR "System32\OpenSSH\ssh.exe"
if ([string]::IsNullOrWhiteSpace($HostName) -or [string]::IsNullOrWhiteSpace($SshUser) -or [string]::IsNullOrWhiteSpace($KeyPath)) {
    throw "Set DP_GAME_SSH_HOST, DP_GAME_SSH_USER, and DP_GAME_SSH_KEY before running this script."
}
$target = "$SshUser@$HostName"

foreach ($requiredPath in @($sshExe, $KeyPath, $KnownHosts)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required SSH file is missing: $requiredPath"
    }
}

$remoteCommand = @'
set -u
printf 'HOST='; hostname
printf 'TIME='; date -Is
printf 'SERVICE='; sudo -n systemctl is-active dp-game.service || true
printf 'ENABLED='; sudo -n systemctl is-enabled dp-game.service || true
printf 'MYSQL='; sudo -n systemctl is-active mysql || true
printf 'CURRENT='; readlink -f /opt/dp-game/current || true
printf 'PORT_8888='; sudo -n ss -lntp | grep ':8888' || true
printf '%s\n' 'MEMORY_SWAP:'; free -h
printf '%s\n' 'DISK:'; df -h /
printf '%s\n' 'TOP_RSS:'; ps -eo pid,comm,%cpu,%mem,rss --sort=-rss | head -n 10
'@

& $sshExe `
    -i $KeyPath `
    -p $Port `
    -o BatchMode=yes `
    -o IdentitiesOnly=yes `
    -o StrictHostKeyChecking=yes `
    -o "UserKnownHostsFile=$KnownHosts" `
    -o ConnectTimeout=15 `
    $target `
    $remoteCommand

exit $LASTEXITCODE
