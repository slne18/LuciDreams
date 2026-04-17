import Capacitor

class BridgeViewController: CAPBridgeViewController {
    override open func capacitorDidLoad() {
        super.capacitorDidLoad()
        // Force-register local plugin even if it is absent from generated packageClassList.
        if let pluginType = NSClassFromString("SystemVolumePlugin") as? CAPPlugin.Type {
            bridge?.registerPluginInstance(pluginType.init())
            return
        }
        // Fallback for namespaced runtime class names.
        if let pluginType = NSClassFromString("App.SystemVolumePlugin") as? CAPPlugin.Type {
            bridge?.registerPluginInstance(pluginType.init())
        }
    }
}
