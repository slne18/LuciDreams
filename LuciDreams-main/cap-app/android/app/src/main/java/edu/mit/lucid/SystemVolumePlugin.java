package edu.mit.lucid;

import android.content.Context;
import android.media.AudioManager;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

@CapacitorPlugin(name = "SystemVolume")
public class SystemVolumePlugin extends Plugin {

    private Double readNormalizedMusicVolume() {
        Context ctx = getContext();
        if (ctx == null) return null;
        AudioManager am = (AudioManager) ctx.getSystemService(Context.AUDIO_SERVICE);
        if (am == null) return null;
        int max = am.getStreamMaxVolume(AudioManager.STREAM_MUSIC);
        int cur = am.getStreamVolume(AudioManager.STREAM_MUSIC);
        if (max <= 0) return null;
        double v = (double) cur / (double) max;
        if (v < 0) v = 0;
        if (v > 1) v = 1;
        return v;
    }

    @PluginMethod
    public void getCurrentVolume(PluginCall call) {
        Double v = readNormalizedMusicVolume();
        if (v == null) {
            call.resolve(new JSObject());
            return;
        }
        JSObject ret = new JSObject();
        ret.put("value", v);
        call.resolve(ret);
    }

    @PluginMethod
    public void getVolume(PluginCall call) {
        getCurrentVolume(call);
    }

    @PluginMethod
    public void getCurrentSystemVolume(PluginCall call) {
        getCurrentVolume(call);
    }
}
