param location string
param nicName string
param privateIp string
param subnetId string
param createPublicIp bool
param publicIpName string

resource pip 'Microsoft.Network/publicIPAddresses@2024-01-01' = if (createPublicIp) {
  name: publicIpName
  location: location
  sku: {
    name: 'Standard'
  }
  properties: {
    publicIPAllocationMethod: 'Static'
  }
}

resource nicWithPublic 'Microsoft.Network/networkInterfaces@2024-01-01' = if (createPublicIp) {
  name: nicName
  location: location
  properties: {
    ipConfigurations: [
      {
        name: 'ipconfig1'
        properties: {
          privateIPAllocationMethod: 'Static'
          privateIPAddress: privateIp
          subnet: {
            id: subnetId
          }
          publicIPAddress: {
            id: pip.id
          }
        }
      }
    ]
  }
}

resource nicPrivate 'Microsoft.Network/networkInterfaces@2024-01-01' = if (!createPublicIp) {
  name: nicName
  location: location
  properties: {
    ipConfigurations: [
      {
        name: 'ipconfig1'
        properties: {
          privateIPAllocationMethod: 'Static'
          privateIPAddress: privateIp
          subnet: {
            id: subnetId
          }
        }
      }
    ]
  }
}

output nicId string = createPublicIp ? nicWithPublic.id : nicPrivate.id
output publicIp string = createPublicIp ? pip!.properties.ipAddress : ''
