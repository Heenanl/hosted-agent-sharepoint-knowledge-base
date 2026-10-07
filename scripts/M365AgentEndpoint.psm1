<#
.SYNOPSIS
    Builds the merge-patch body that enables Microsoft 365 traffic on a
    private-network Foundry agent's Activity Protocol route.

.DESCRIPTION
    `PATCH /agents/{name}` REPLACES the whole `protocol_configuration` and
    `authorization_schemes` bags. Anything omitted is dropped, so the body must be
    built from the agent's current state rather than written from scratch.

    Ref: https://learn.microsoft.com/azure/foundry/agents/how-to/configure-agent#allow-microsoft-365-traffic-to-a-private-network-agent
#>

Set-StrictMode -Version Latest

$script:BotServiceSchemes = @('BotServiceRbac', 'BotServiceTenant')

function ConvertTo-HashtableDeep {
    <#
    .SYNOPSIS
        Converts `ConvertFrom-Json` output into nested hashtables so the patch can
        be rebuilt without losing unknown properties.
    #>
    [CmdletBinding()]
    param([Parameter(Position = 0)]$InputObject)

    if ($null -eq $InputObject) { return $null }

    if ($InputObject -is [System.Collections.IDictionary]) {
        $map = @{}
        foreach ($key in @($InputObject.Keys)) {
            $map[$key] = ConvertTo-HashtableDeep $InputObject[$key]
        }
        return $map
    }

    if ($InputObject -is [System.Management.Automation.PSCustomObject]) {
        $map = @{}
        foreach ($property in $InputObject.PSObject.Properties) {
            $map[$property.Name] = ConvertTo-HashtableDeep $property.Value
        }
        return $map
    }

    if ($InputObject -is [string] -or $InputObject -is [ValueType]) { return $InputObject }

    if ($InputObject -is [System.Collections.IEnumerable]) {
        return @(foreach ($item in $InputObject) { ConvertTo-HashtableDeep $item })
    }

    return $InputObject
}

function New-AgentEndpointPatch {
    <#
    .SYNOPSIS
        Returns the `agent_endpoint` merge-patch body, preserving every protocol and
        authorization scheme the endpoint already has.

    .PARAMETER ProtocolConfiguration
        The agent's current `agent_endpoint.protocol_configuration`.

    .PARAMETER AuthorizationSchemes
        The agent's current `agent_endpoint.authorization_schemes`.

    .PARAMETER AuthorizationScheme
        Bot Service scheme to apply. Publishing replaces a different Bot Service
        scheme, so this mirrors the publish scope: Shared/Personal -> BotServiceRbac,
        Tenant -> BotServiceTenant. Omit it to keep whatever Bot Service scheme the
        endpoint already has, so a rollback cannot silently re-scope a tenant-published
        agent; BotServiceRbac is used only when the endpoint has none.

    .PARAMETER EnableM365PublicEndpoint
        Value for `activity.enable_m365_public_endpoint`. Pass $false to roll back.
    #>
    [CmdletBinding()]
    param(
        [Parameter()]$ProtocolConfiguration,
        [Parameter()]$AuthorizationSchemes,

        [ValidateSet('BotServiceRbac', 'BotServiceTenant')]
        [string]$AuthorizationScheme,

        [bool]$EnableM365PublicEndpoint = $true
    )

    $protocols = ConvertTo-HashtableDeep $ProtocolConfiguration
    if ($null -eq $protocols) { $protocols = @{} }

    # Teams and Microsoft 365 deliver messages over the activity protocol, so it must
    # exist before the network exception means anything.
    if (-not $protocols.ContainsKey('activity') -or $null -eq $protocols['activity']) {
        $protocols['activity'] = @{}
    }
    $protocols['activity']['enable_m365_public_endpoint'] = $EnableM365PublicEndpoint

    $existingScheme = $null
    $schemes = @()
    foreach ($scheme in @(ConvertTo-HashtableDeep $AuthorizationSchemes)) {
        if ($null -eq $scheme) { continue }
        $type = if ($scheme -is [System.Collections.IDictionary] -and $scheme.ContainsKey('type')) { $scheme['type'] } else { $null }
        # A single Bot Service scheme is authoritative; the requested one replaces it.
        if ($type -and $script:BotServiceSchemes -contains $type) {
            if (-not $existingScheme) { $existingScheme = $type }
            continue
        }
        $schemes += , $scheme
    }

    $effectiveScheme = if ($AuthorizationScheme) { $AuthorizationScheme }
    elseif ($existingScheme) { $existingScheme }
    else { 'BotServiceRbac' }
    $schemes += , @{ type = $effectiveScheme }

    return @{
        agent_endpoint = @{
            protocol_configuration = $protocols
            authorization_schemes  = $schemes
        }
    }
}

function ConvertTo-MergePatchJson {
    <#
    .SYNOPSIS
        Serializes the patch body for `Content-Type: application/merge-patch+json`.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory, Position = 0)]$Patch)

    return ConvertTo-Json -InputObject $Patch -Depth 25
}

Export-ModuleMember -Function New-AgentEndpointPatch, ConvertTo-MergePatchJson, ConvertTo-HashtableDeep
