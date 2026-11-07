param location string
param operatorCidr string
param vnetName string = 'scylla-vnet'
param addressPrefix string = '10.0.0.0/16'
param subnetPrefix string = '10.0.1.0/24'

resource nsg 'Microsoft.Network/networkSecurityGroups@2024-01-01' = {
  name: 'scylla-nsg'
  location: location
  properties: {
    securityRules: [
      {
        name: 'operator-cql'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: operatorCidr
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '9042'
        }
      }
      {
        name: 'operator-grafana'
        properties: {
          priority: 110
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: operatorCidr
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '3000'
        }
      }
      {
        name: 'operator-prometheus'
        properties: {
          priority: 120
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: operatorCidr
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '9090'
        }
      }
      {
        name: 'operator-manager'
        properties: {
          priority: 130
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: operatorCidr
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '5080'
        }
      }
      {
        name: 'operator-ssh'
        properties: {
          priority: 140
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: operatorCidr
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '22'
        }
      }
    ]
  }
}

resource vnet 'Microsoft.Network/virtualNetworks@2024-01-01' = {
  name: vnetName
  location: location
  properties: {
    addressSpace: {
      addressPrefixes: [
        addressPrefix
      ]
    }
    subnets: [
      {
        name: 'scylla'
        properties: {
          addressPrefix: subnetPrefix
          networkSecurityGroup: {
            id: nsg.id
          }
        }
      }
    ]
  }
}

output subnetId string = vnet.properties.subnets[0].id
output nsgId string = nsg.id
