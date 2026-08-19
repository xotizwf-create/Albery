<#
.SYNOPSIS
    Пускает трафик Авито мимо VPN — напрямую с российского домашнего адреса.

.DESCRIPTION
    На машине владельца поднят AmneziaVPN с маршрутом по умолчанию (метрика интерфейса 5
    перебивает Ethernet), поэтому наружу всё уходит с эстонского адреса. Авито с него
    отдаёт «Доступ ограничен: проблема с IP», а с домашнего российского работает.

    Скрипт добавляет ПОСТОЯННЫЕ (переживают перезагрузку) маршруты на подсети Авито через
    физический шлюз. Остальной трафик машины остаётся в туннеле — правится только Авито.

    Подсети взяты из живого DNS, а не из чужих списков: avito.ru, www/m/api/auth/ws.avito.ru
    и avito.st резолвятся в 176.114.120.x / .122.x / .124.x — это диапазон 176.114.120.0/21.
    Плюс две узкие сети статики. Широкие CDN-диапазоны сюда класть нельзя: вместе с нужным
    сервисом мимо VPN уедет половина интернета.

.PARAMETER Remove
    Убрать маршруты и вернуть Авито в туннель.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\avito_direct_routes.ps1
    powershell -ExecutionPolicy Bypass -File scripts\avito_direct_routes.ps1 -Remove

.NOTES
    Нужны права администратора: изменение таблицы маршрутов.
#>
[CmdletBinding()]
param(
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'

# Сети Авито (проверено резолвом 19.08.2026) + узкие сети статики.
#
# Формы префиксов НЕ случайны. AmneziaVPN держит эти же сети у себя: 176.114.120.0/21,
# 151.236.94.0/24 и 95.181.182.0/24 с метрикой 5, а физический Ethernet идёт с метрикой 25.
# При РАВНОЙ длине префикса Windows выбирает меньшую метрику, поэтому маршрут «как у VPN,
# но через роутер» проигрывает и Авито продолжает уходить в туннель (проверено: исходящий
# адрес оставался 10.8.1.1). Длина префикса важнее метрики, поэтому каждая сеть перекрыта
# более узкой: /21 → двумя /22, каждая /24 → двумя /25. Покрытие то же, приоритет — наш.
$AvitoNetworks = @(
    @{ Prefix = '176.114.120.0/22';   Note = 'avito.ru, m/api/auth/ws.avito.ru, avito.st' },
    @{ Prefix = '176.114.124.0/22';   Note = 'вторая половина сети Авито' },
    @{ Prefix = '151.236.94.0/25';    Note = 'www.avito.st — статика' },
    @{ Prefix = '151.236.94.128/25';  Note = 'www.avito.st — статика' },
    @{ Prefix = '95.181.182.0/25';    Note = 'static.avito.ru — статика' },
    @{ Prefix = '95.181.182.128/25';  Note = 'static.avito.ru — статика' }
)

function Invoke-Route {
    <#  route.exe пишет в stderr обычные сообщения («Элемент не найден» на удалении
        несуществующего маршрута). При $ErrorActionPreference = 'Stop' это валит скрипт,
        поэтому вызовы идут через обёртку: она возвращает текст и код возврата, а решение
        принимает вызывающий.  #>
    param([Parameter(Mandatory)][string[]]$Arguments)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & route.exe @Arguments 2>&1
        return [pscustomobject]@{ Output = ($output -join ' ').Trim(); ExitCode = $LASTEXITCODE }
    } finally {
        $ErrorActionPreference = $previous
    }
}

function ConvertTo-IPv4Mask {
    param([Parameter(Mandatory)][int]$PrefixLength)
    $bits = ([uint32]::MaxValue) -shl (32 - $PrefixLength)
    $bytes = [BitConverter]::GetBytes([uint32]$bits)
    [Array]::Reverse($bytes)
    return ($bytes -join '.')
}

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]$identity
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Нужны права администратора: запустите PowerShell от имени администратора.'
    }
}

function Get-PhysicalRoute {
    <#  Физический выход = маршрут по умолчанию НЕ через VPN-адаптер.
        Ищем по шлюзу: у туннеля next hop 0.0.0.0, у физической сети — адрес роутера.  #>
    $candidates = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction Stop |
        Where-Object { $_.NextHop -ne '0.0.0.0' } |
        Sort-Object -Property RouteMetric, InterfaceMetric

    foreach ($route in $candidates) {
        $adapter = Get-NetAdapter -InterfaceIndex $route.InterfaceIndex -ErrorAction SilentlyContinue
        if ($adapter -and $adapter.Status -eq 'Up' -and
            $adapter.InterfaceDescription -notmatch 'VPN|WireGuard|Amnezia|TAP|Tailscale') {
            return $route
        }
    }
    throw 'Не нашёл физический маршрут по умолчанию — проверьте, что кабель/Wi-Fi подключены.'
}

Assert-Administrator
$physical = Get-PhysicalRoute
$adapterName = (Get-NetAdapter -InterfaceIndex $physical.InterfaceIndex).Name

if ($Remove) {
    Write-Host "Возвращаю Авито в туннель (интерфейс «$adapterName»)..."
    foreach ($network in $AvitoNetworks) {
        $address, $length = $network.Prefix.Split('/')
        $mask = ConvertTo-IPv4Mask ([int]$length)
        $deleted = Invoke-Route @('delete', $address, 'mask', $mask)
        if ($deleted.ExitCode -eq 0) {
            Write-Host ("  убран {0}" -f $network.Prefix)
        } else {
            Write-Host ("  {0} — маршрута не было" -f $network.Prefix)
        }
    }
    Write-Host 'Готово: Авито снова ходит через VPN.'
    return
}

Write-Host ("Физический выход: «{0}», шлюз {1}, адрес {2}" -f `
    $adapterName, $physical.NextHop, (Get-NetIPAddress -InterfaceIndex $physical.InterfaceIndex `
        -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike '169.254.*' } |
        Select-Object -First 1 -ExpandProperty IPAddress))

foreach ($network in $AvitoNetworks) {
    $address, $length = $network.Prefix.Split('/')
    $mask = ConvertTo-IPv4Mask ([int]$length)
    # Классический route.exe, а не New-NetRoute: на этой сборке Windows постоянное хранилище
    # через New-NetRoute отвергается («Invalid parameter PolicyStore PersistentStore»),
    # а `route -p add` кладёт маршрут сразу и в живую таблицу, и в постоянную.
    $null = Invoke-Route @('delete', $address, 'mask', $mask)
    $added = Invoke-Route @('-p', 'add', $address, 'mask', $mask, $physical.NextHop,
                            'metric', '1', 'if', "$($physical.InterfaceIndex)")
    if ($added.ExitCode -ne 0) {
        throw ("Не удалось добавить маршрут {0}: {1}" -f $network.Prefix, $added.Output)
    }
    Write-Host ("  {0} -> напрямую ({1})" -f $network.Prefix, $network.Note)
}

Write-Host ''
Write-Host 'Проверка:'
$probe = Find-NetRoute -RemoteIPAddress '176.114.122.24' -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($probe) {
    $probeAdapter = (Get-NetAdapter -InterfaceIndex $probe.InterfaceIndex -ErrorAction SilentlyContinue).Name
    Write-Host ("  до avito.ru пойдём через «{0}»" -f $probeAdapter)
}
$sourceIp = (Find-NetRoute -RemoteIPAddress '176.114.122.24' -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress } | Select-Object -First 1 -ExpandProperty IPAddress)
if ($sourceIp) { Write-Host ("  исходящий адрес: {0}" -f $sourceIp) }
Write-Host '  остальной трафик остался в VPN.'
