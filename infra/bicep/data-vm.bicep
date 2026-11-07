param location string
param vmName string
param nicId string
param vmSize string
param sshPublicKey string
@secure()
param customData string
param zone string = ''
param adminUsername string = 'scylla'
param osDiskGb int = 32
param dataDiskGb int = 256
param identityId string
param ubuntuPublisher string
param ubuntuOffer string
param ubuntuSku string
param ubuntuVersion string

resource vm 'Microsoft.Compute/virtualMachines@2024-07-01' = {
  name: vmName
  location: location
  zones: empty(zone) ? [] : [
    zone
  ]
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityId}': {}
    }
  }
  properties: {
    hardwareProfile: {
      vmSize: vmSize
    }
    osProfile: {
      computerName: vmName
      adminUsername: adminUsername
      customData: base64(customData)
      linuxConfiguration: {
        disablePasswordAuthentication: true
        ssh: {
          publicKeys: [
            {
              path: '/home/${adminUsername}/.ssh/authorized_keys'
              keyData: sshPublicKey
            }
          ]
        }
      }
    }
    storageProfile: {
      imageReference: {
        publisher: ubuntuPublisher
        offer: ubuntuOffer
        sku: ubuntuSku
        version: ubuntuVersion
      }
      osDisk: {
        createOption: 'FromImage'
        diskSizeGB: osDiskGb
        deleteOption: 'Delete'
        managedDisk: {
          storageAccountType: 'Premium_LRS'
        }
      }
      dataDisks: [
        {
          lun: 0
          createOption: 'Empty'
          diskSizeGB: dataDiskGb
          deleteOption: 'Delete'
          managedDisk: {
            storageAccountType: 'Premium_LRS'
          }
        }
      ]
    }
    networkProfile: {
      networkInterfaces: [
        {
          id: nicId
          properties: {
            deleteOption: 'Detach'
          }
        }
      ]
    }
  }
}

output vmId string = vm.id
