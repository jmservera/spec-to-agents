param name string
param location string = resourceGroup().location
param tags object = {}
param appSettings array = []
param serviceName string = 'app'
param identityId string
param containerAppsEnvironmentId string
param containerRegistryName string


resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: containerRegistryName
}


// Deploy Container App
module containerApp 'br/public:avm/res/app/container-app:0.11.0' = {
  name: '${serviceName}-container-app'
  params: {
    name: name
    location: location
    tags: union(tags, { 'azd-service-name': serviceName })
    environmentResourceId: containerAppsEnvironmentId
    managedIdentities: {
      userAssignedResourceIds: [identityId]
    }
    registries: [
      {
        server: containerRegistry.properties.loginServer
        identity: identityId
      }
    ]
    containers: [
      {
        name: 'main'
        // Use a placeholder image during initial provisioning, azd will update this during deployment
        image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
        resources: {
          cpu: json('0.5')
          memory: '1Gi'
        }
        env: appSettings
      }
    ]
    ingressTargetPort: 8080
    ingressExternal: true
    ingressTransport: 'auto'
    scaleMinReplicas: 1
    scaleMaxReplicas: 3
  }
}

output SERVICE_APP_NAME string = containerApp.outputs.name
output SERVICE_APP_URI string = containerApp.outputs.fqdn
output resourceId string = containerApp.outputs.resourceId
