// Create Container Apps Environment
param resourceToken string
param applicationInsightsName string = ''
param location string = resourceGroup().location
param tags object = {}

resource applicationInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: applicationInsightsName
}
module containerAppsEnvironment 'br/public:avm/res/app/managed-environment:0.8.2' = {
  name: 'container-apps-environment'
  params: {
    name: 'cae-${resourceToken}'
    location: location
    tags: tags
    logAnalyticsWorkspaceResourceId: applicationInsights.properties.WorkspaceResourceId
    zoneRedundant: false  // Disable zone redundancy since we're not using VNet
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

output environmentId string = containerAppsEnvironment.outputs.resourceId
