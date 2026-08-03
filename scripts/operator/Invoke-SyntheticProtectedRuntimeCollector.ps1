# HC-309-R4D synthetic-only collector foundation.
# Development/test use only. It cannot collect or authenticate live evidence.

$script:Schema = 'hc.protected_runtime.synthetic_fixture.v1'
$script:OutputSchema = 'hc.protected_runtime.synthetic_envelope.v1'
$script:CollectorVersion = '0.1.0-synthetic'
$script:MaxBytes = 65536
$script:InputDeadlineMilliseconds = 10000
$script:MaxDepth = 8
$script:MaxContainers = 32
$script:MaxMembers = 16
$script:MaxArrayElements = 16
$script:MaxStringLength = 256
$script:MaxScalars = 128
$script:Registry = @(
    'repository_commit',
    'active_release',
    'release_manifest',
    'installer_provenance',
    'interpreter_provenance',
    'dependency_provenance',
    'task_host',
    'task_proxy',
    'health_8743_healthz',
    'health_8743_readyz',
    'health_8744_healthz',
    'health_8744_readyz'
)

function script:Stop-HcSynthetic([int]$Code, [string]$Json) {
    [Console]::Out.WriteLine($Json)
    [Environment]::Exit($Code)
}

function script:Fail-HcSyntheticConfiguration {
    script:Stop-HcSynthetic 22 '{"authentication_status":"unavailable","certification_status":"FAIL","environment":"synthetic","error":"synthetic_configuration_invalid","exit_code":22,"schema_version":"hc.protected_runtime.synthetic_envelope.v1"}'
}

function script:Skip-HcWhitespace([hashtable]$State) {
    while ($State.Index -lt $State.Text.Length) {
        $c = $State.Text[$State.Index]
        if ($c -ne ' ' -and $c -ne "`t" -and $c -ne "`r" -and $c -ne "`n") { break }
        $State.Index++
    }
}

function script:Read-HcJsonString([hashtable]$State) {
    if ($State.Index -ge $State.Text.Length -or $State.Text[$State.Index] -ne '"') { throw 'invalid' }
    $State.Index++
    $builder = [System.Text.StringBuilder]::new()
    while ($State.Index -lt $State.Text.Length) {
        $c = $State.Text[$State.Index++]
        if ($c -eq '"') {
            $value = $builder.ToString()
            if ($value.Length -gt $script:MaxStringLength) { throw 'invalid' }
            return $value
        }
        if ([int][char]$c -lt 32) { throw 'invalid' }
        if ($c -ne '\') {
            [void]$builder.Append($c)
            if ($builder.Length -gt $script:MaxStringLength) { throw 'invalid' }
            continue
        }
        if ($State.Index -ge $State.Text.Length) { throw 'invalid' }
        $escape = $State.Text[$State.Index++]
        switch ($escape) {
            '"' { [void]$builder.Append('"') }
            '\' { [void]$builder.Append('\') }
            '/' { [void]$builder.Append('/') }
            'b' { [void]$builder.Append([char]8) }
            'f' { [void]$builder.Append([char]12) }
            'n' { [void]$builder.Append([char]10) }
            'r' { [void]$builder.Append([char]13) }
            't' { [void]$builder.Append([char]9) }
            'u' {
                if ($State.Index + 4 -gt $State.Text.Length) { throw 'invalid' }
                $hex = $State.Text.Substring($State.Index, 4)
                if ($hex -notmatch '^[0-9A-Fa-f]{4}$') { throw 'invalid' }
                $unit = [Convert]::ToInt32($hex, 16)
                $State.Index += 4
                if ($unit -ge 0xD800 -and $unit -le 0xDBFF) {
                    if ($State.Index + 6 -gt $State.Text.Length -or $State.Text[$State.Index] -ne '\' -or $State.Text[$State.Index + 1] -ne 'u') { throw 'invalid' }
                    $lowHex = $State.Text.Substring($State.Index + 2, 4)
                    if ($lowHex -notmatch '^[0-9A-Fa-f]{4}$') { throw 'invalid' }
                    $low = [Convert]::ToInt32($lowHex, 16)
                    if ($low -lt 0xDC00 -or $low -gt 0xDFFF) { throw 'invalid' }
                    [void]$builder.Append([char]$unit).Append([char]$low)
                    $State.Index += 6
                }
                elseif ($unit -ge 0xDC00 -and $unit -le 0xDFFF) {
                    throw 'invalid'
                }
                else {
                    [void]$builder.Append([char]$unit)
                }
            }
            default { throw 'invalid' }
        }
        if ($builder.Length -gt $script:MaxStringLength) { throw 'invalid' }
    }
    throw 'invalid'
}

function script:Read-HcJsonValue([hashtable]$State, [int]$Depth) {
    if ($Depth -gt $script:MaxDepth) { throw 'invalid' }
    script:Skip-HcWhitespace $State
    if ($State.Index -ge $State.Text.Length) { throw 'invalid' }
    $c = $State.Text[$State.Index]
    if ($c -eq '{') { return script:Read-HcJsonObject $State $Depth }
    if ($c -eq '[') { return script:Read-HcJsonArray $State $Depth }
    if ($c -eq '"') {
        $State.Scalars++
        if ($State.Scalars -gt $script:MaxScalars) { throw 'invalid' }
        return script:Read-HcJsonString $State
    }
    $remaining = $State.Text.Substring($State.Index)
    foreach ($literal in @('true', 'false', 'null')) {
        if ($remaining.StartsWith($literal, [StringComparison]::Ordinal)) {
            $State.Index += $literal.Length
            $State.Scalars++
            if ($State.Scalars -gt $script:MaxScalars) { throw 'invalid' }
            if ($literal -eq 'true') { return $true }
            if ($literal -eq 'false') { return $false }
            return $null
        }
    }
    $match = [Text.RegularExpressions.Regex]::Match($remaining, '^-?(0|[1-9][0-9]*)')
    if (-not $match.Success) { throw 'invalid' }
    $next = $match.Length
    if ($next -lt $remaining.Length -and $remaining[$next] -notin @(' ', "`t", "`r", "`n", ',', '}', ']')) { throw 'invalid' }
    $number = [long]0
    if (-not [long]::TryParse($match.Value, [Globalization.NumberStyles]::AllowLeadingSign, [Globalization.CultureInfo]::InvariantCulture, [ref]$number)) { throw 'invalid' }
    $State.Index += $match.Length
    $State.Scalars++
    if ($State.Scalars -gt $script:MaxScalars) { throw 'invalid' }
    return $number
}

function script:Read-HcJsonObject([hashtable]$State, [int]$Depth) {
    $State.Containers++
    if ($State.Containers -gt $script:MaxContainers) { throw 'invalid' }
    $State.Index++
    $result = [ordered]@{}
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    script:Skip-HcWhitespace $State
    if ($State.Index -lt $State.Text.Length -and $State.Text[$State.Index] -eq '}') { $State.Index++; return $result }
    $members = 0
    while ($true) {
        script:Skip-HcWhitespace $State
        $key = script:Read-HcJsonString $State
        if (-not $seen.Add($key)) { throw 'invalid' }
        $members++
        if ($members -gt $script:MaxMembers) { throw 'invalid' }
        script:Skip-HcWhitespace $State
        if ($State.Index -ge $State.Text.Length -or $State.Text[$State.Index] -ne ':') { throw 'invalid' }
        $State.Index++
        $result[$key] = script:Read-HcJsonValue $State ($Depth + 1)
        script:Skip-HcWhitespace $State
        if ($State.Index -ge $State.Text.Length) { throw 'invalid' }
        $separator = $State.Text[$State.Index++]
        if ($separator -eq '}') { return $result }
        if ($separator -ne ',') { throw 'invalid' }
    }
}

function script:Read-HcJsonArray([hashtable]$State, [int]$Depth) {
    $State.Containers++
    if ($State.Containers -gt $script:MaxContainers) { throw 'invalid' }
    $State.Index++
    $result = [Collections.ArrayList]::new()
    script:Skip-HcWhitespace $State
    if ($State.Index -lt $State.Text.Length -and $State.Text[$State.Index] -eq ']') { $State.Index++; return $result }
    while ($true) {
        if ($result.Count -ge $script:MaxArrayElements) { throw 'invalid' }
        [void]$result.Add((script:Read-HcJsonValue $State ($Depth + 1)))
        script:Skip-HcWhitespace $State
        if ($State.Index -ge $State.Text.Length) { throw 'invalid' }
        $separator = $State.Text[$State.Index++]
        if ($separator -eq ']') { return $result }
        if ($separator -ne ',') { throw 'invalid' }
    }
}

function script:Assert-HcExactKeys($Object, [string[]]$Expected) {
    if ($Object -isnot [Collections.IDictionary]) { throw 'invalid' }
    if ($Object.Count -ne $Expected.Count) { throw 'invalid' }
    foreach ($key in $Expected) { if (-not $Object.Contains($key)) { throw 'invalid' } }
}

function script:Read-HcSyntheticInput {
    $stream = [Console]::OpenStandardInput()
    $bytes = [byte[]]::new($script:MaxBytes + 1)
    $total = 0
    $deadline = [Threading.CancellationTokenSource]::new()
    try {
        # One process-wide deadline covers every read. The deadline task lets
        # Windows PowerShell 5.1 stop waiting even when a redirected pipe does
        # not honor cancellation of its outstanding asynchronous read.
        $deadline.CancelAfter($script:InputDeadlineMilliseconds)
        $deadlineTask = [Threading.Tasks.Task]::Delay(-1, $deadline.Token)
        while ($total -lt $bytes.Length) {
            $count = [Math]::Min(8192, $bytes.Length - $total)
            $readTask = $stream.ReadAsync($bytes, $total, $count, $deadline.Token)
            $winner = [Threading.Tasks.Task]::WhenAny(
                [Threading.Tasks.Task[]]@($readTask, $deadlineTask)
            ).GetAwaiter().GetResult()
            if ([object]::ReferenceEquals($winner, $deadlineTask)) { throw 'invalid' }
            $read = $readTask.GetAwaiter().GetResult()
            if ($read -eq 0) { break }
            $total += $read
        }
    }
    finally {
        $deadline.Dispose()
    }
    if ($total -gt $script:MaxBytes) { throw 'invalid' }
    $offset = 0
    if ($total -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) { $offset = 3 }
    $utf8 = [Text.UTF8Encoding]::new($false, $true)
    $text = $utf8.GetString($bytes, $offset, $total - $offset)
    $state = @{ Text = $text; Index = 0; Containers = 0; Scalars = 0 }
    $value = script:Read-HcJsonValue $state 1
    script:Skip-HcWhitespace $state
    if ($state.Index -ne $text.Length) { throw 'invalid' }
    return $value
}

function script:Assert-HcSyntheticFixture($Fixture) {
    script:Assert-HcExactKeys $Fixture @('schema_version', 'checks')
    if ($Fixture.schema_version -cne $script:Schema) { throw 'invalid' }
    if ($Fixture.checks -isnot [Collections.IList] -or $Fixture.checks.Count -ne $script:Registry.Count) { throw 'invalid' }
    $byId = @{}
    foreach ($check in $Fixture.checks) {
        script:Assert-HcExactKeys $check @('id', 'status', 'reason')
        if ($check.id -isnot [string] -or $script:Registry -cnotcontains $check.id -or $byId.ContainsKey($check.id)) { throw 'invalid' }
        if ($check.status -isnot [string] -or $check.status -cnotin @('PASS', 'BLOCKED', 'FAIL')) { throw 'invalid' }
        $expectedReason = if ($check.status -ceq 'PASS') { 'ok' } elseif ($check.status -ceq 'BLOCKED') { 'synthetic_unavailable' } else { $check.id + '_mismatch' }
        if ($check.reason -isnot [string] -or $check.reason -cne $expectedReason) { throw 'invalid' }
        $byId[$check.id] = $check
    }
    foreach ($id in $script:Registry) { if (-not $byId.ContainsKey($id)) { throw 'invalid' } }
    return $byId
}

function script:Write-HcSyntheticEnvelope($Fixture, [hashtable]$ById) {
    $failed = $false
    foreach ($id in $script:Registry) { if ($ById[$id].status -ceq 'FAIL') { $failed = $true } }
    $certification = if ($failed) { 'FAIL' } else { 'BLOCKED' }
    $exitCode = if ($failed) { 21 } else { 20 }
    $builder = [Text.StringBuilder]::new()
    [void]$builder.Append('{"authentication_status":"unavailable","certification_status":"').Append($certification)
    [void]$builder.Append('","checks":[')
    for ($index = 0; $index -lt $script:Registry.Count; $index++) {
        if ($index -gt 0) { [void]$builder.Append(',') }
        $check = $ById[$script:Registry[$index]]
        [void]$builder.Append('{"id":"').Append($check.id).Append('","reason":"').Append($check.reason).Append('","status":"').Append($check.status).Append('"}')
    }
    [void]$builder.Append('],"collector":{"artifact_identifier":"synthetic-untrusted","version":"').Append($script:CollectorVersion).Append('"}')
    [void]$builder.Append(',"environment":"synthetic","exit_code":').Append($exitCode).Append(',"schema_version":"').Append($script:OutputSchema).Append('"}')
    script:Stop-HcSynthetic $exitCode $builder.ToString()
}

try {
    if ($args.Count -ne 0) { throw 'invalid' }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'invalid' }
    $fixture = script:Read-HcSyntheticInput
    $checks = script:Assert-HcSyntheticFixture $fixture
    script:Write-HcSyntheticEnvelope $fixture $checks
}
catch {
    script:Fail-HcSyntheticConfiguration
}
