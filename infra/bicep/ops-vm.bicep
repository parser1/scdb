param location string
param vmName string
param nicId string
param vmSize string
param sshPublicKey string
@secure()
param customData string
param zone string = ''
param adminUsername string = 'scylla'
param osDiskGb int = 64
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
