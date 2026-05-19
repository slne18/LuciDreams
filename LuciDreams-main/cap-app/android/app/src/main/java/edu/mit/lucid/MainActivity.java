package edu.mit.lucid;

import android.Manifest;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.webkit.PermissionRequest;

import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;

import com.getcapacitor.BridgeActivity;
import com.getcapacitor.BridgeWebChromeClient;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;

public class MainActivity extends BridgeActivity {
    private static final int WEB_PERMISSION_REQUEST_CODE = 4319;
    private PermissionRequest pendingWebPermissionRequest;

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        configureWebPermissionBridge();
    }

    private void configureWebPermissionBridge() {
        if (getBridge() == null || getBridge().getWebView() == null) {
            return;
        }
        getBridge().getWebView().setWebChromeClient(new BridgeWebChromeClient(getBridge()) {
            @Override
            public void onPermissionRequest(final PermissionRequest request) {
                runOnUiThread(() -> handleWebPermissionRequest(request));
            }
        });
    }

    private void handleWebPermissionRequest(PermissionRequest request) {
        List<String> grantedResources = new ArrayList<>();
        LinkedHashSet<String> missingAndroidPermissions = new LinkedHashSet<>();
        for (String resource : request.getResources()) {
            if (PermissionRequest.RESOURCE_AUDIO_CAPTURE.equals(resource)) {
                if (hasAndroidPermission(Manifest.permission.RECORD_AUDIO)) {
                    grantedResources.add(resource);
                } else {
                    missingAndroidPermissions.add(Manifest.permission.RECORD_AUDIO);
                }
            } else if (PermissionRequest.RESOURCE_VIDEO_CAPTURE.equals(resource)) {
                if (hasAndroidPermission(Manifest.permission.CAMERA)) {
                    grantedResources.add(resource);
                } else {
                    missingAndroidPermissions.add(Manifest.permission.CAMERA);
                }
            }
        }
        if (!missingAndroidPermissions.isEmpty()) {
            pendingWebPermissionRequest = request;
            ActivityCompat.requestPermissions(
                this,
                missingAndroidPermissions.toArray(new String[0]),
                WEB_PERMISSION_REQUEST_CODE
            );
            return;
        }
        finalizeWebPermissionRequest(request, grantedResources);
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode != WEB_PERMISSION_REQUEST_CODE) {
            return;
        }
        PermissionRequest request = pendingWebPermissionRequest;
        pendingWebPermissionRequest = null;
        if (request == null) {
            return;
        }
        List<String> grantedResources = new ArrayList<>();
        for (String resource : request.getResources()) {
            if (PermissionRequest.RESOURCE_AUDIO_CAPTURE.equals(resource)
                    && hasAndroidPermission(Manifest.permission.RECORD_AUDIO)) {
                grantedResources.add(resource);
            } else if (PermissionRequest.RESOURCE_VIDEO_CAPTURE.equals(resource)
                    && hasAndroidPermission(Manifest.permission.CAMERA)) {
                grantedResources.add(resource);
            }
        }
        finalizeWebPermissionRequest(request, grantedResources);
    }

    private void finalizeWebPermissionRequest(PermissionRequest request, List<String> grantedResources) {
        try {
            if (grantedResources.isEmpty()) {
                request.deny();
            } else {
                request.grant(grantedResources.toArray(new String[0]));
            }
        } catch (Throwable ignored) {
            // WebView may invalidate the request if user navigates away.
        }
    }

    private boolean hasAndroidPermission(String permission) {
        return ContextCompat.checkSelfPermission(this, permission) == PackageManager.PERMISSION_GRANTED;
    }
}
