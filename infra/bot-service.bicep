// =============================================================================
//  bot-service.bicep
//  Azure Bot Service + Microsoft Teams channel for one Foundry agent.
//  Deployed by scripts/Publish-AgentToTeams.ps1 before it calls the Foundry
//  Microsoft 365 publish API.
//
//  The bot's messaging endpoint is the agent's own Activity Protocol route.
//
//  Ref: https://learn.microsoft.com/azure/foundry/agents/how-to/publish-copilot-virtual-network
// =============================================================================

targetScope = 'resourceGroup'

@description('Azure Bot Service resource name (globally unique across Azure).')
param botName string

@description('Display name shown to users.')
param displayName string

@description('Agent identity client ID (instance_identity.client_id from the Foundry Get agent API).')
param msaAppId string

@description('Microsoft Entra tenant ID.')
param tenantId string

@description('Bot messaging endpoint: the agent\'s Activity Protocol route.')
param endpoint string

@description('Bot Service SKU. F0 (free) is sufficient for a single Teams channel.')
@allowed([
  'F0'
  'S1'
])
param botServiceSku string = 'F0'

@description('Disable public network access to the bot resource itself (management/Direct Line). The Teams channel still reaches the messaging endpoint.')
@allowed([
  'Enabled'
  'Disabled'
])
param publicNetworkAccess string = 'Disabled'

@description('Tags applied to the bot resource.')
param tags object = {}

resource botService 'Microsoft.BotService/botServices@2022-09-15' = {
  name: botName
  kind: 'azurebot'
  location: 'global'
  tags: tags
  sku: {
    name: botServiceSku
  }
  properties: {
    displayName: displayName
    endpoint: endpoint
    msaAppId: msaAppId
    msaAppTenantId: tenantId
    msaAppType: 'SingleTenant'
    publicNetworkAccess: publicNetworkAccess
  }
}

resource botServiceMsTeamsChannel 'Microsoft.BotService/botServices/channels@2022-09-15' = {
  parent: botService
  name: 'MsTeamsChannel'
  location: 'global'
  properties: {
    channelName: 'MsTeamsChannel'
  }
}

@description('ARM resource ID of the bot (pass as botServiceArmId when publishing).')
output botServiceArmId string = botService.id

@description('Bot resource name.')
output botName string = botService.name
