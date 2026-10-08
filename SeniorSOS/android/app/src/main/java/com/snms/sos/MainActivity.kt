package com.snms.sos

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Notification
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.net.http.SslError
import android.os.Build
import android.os.Bundle
import android.webkit.GeolocationPermissions
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import android.widget.Toast

class MainActivity : Activity() {
    private lateinit var web: WebView
    private lateinit var lm: LocationManager
    private var loc: Location? = null
    private var nid = 1

    private val ll = object : LocationListener {
        override fun onLocationChanged(l: Location) { loc = l }
        @Deprecated("old API") override fun onStatusChanged(p: String?, s: Int, e: Bundle?) {}
        override fun onProviderEnabled(p: String) {}
        override fun onProviderDisabled(p: String) {}
    }

    private fun server(): String =
        getSharedPreferences("c", 0).getString("url", "http://10.0.2.2:5000")!!

    private fun granted(p: String) = checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED

    inner class Bridge {
        @JavascriptInterface fun getLocation(): String = loc?.let { "${it.latitude},${it.longitude}" } ?: ""
        @JavascriptInterface fun changeServer() { runOnUiThread { askServer("Server address") } }
        @JavascriptInterface fun notify(text: String) { runOnUiThread { push(text) } }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        lm = getSystemService(Context.LOCATION_SERVICE) as LocationManager
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel("sos", "SOS alerts", NotificationManager.IMPORTANCE_HIGH))

        web = WebView(this)
        setContentView(web)
        WebView.setWebContentsDebuggingEnabled(true)
        web.settings.javaScriptEnabled = true
        web.settings.domStorageEnabled = true
        web.settings.setGeolocationEnabled(true)
        web.addJavascriptInterface(Bridge(), "Android")
        web.webViewClient = object : WebViewClient() {
            override fun onReceivedError(v: WebView, r: WebResourceRequest, e: WebResourceError) {
                if (r.isForMainFrame) askServer("Can't reach the server. Check the address and that the server is running.")
            }
        }
        web.webChromeClient = object : WebChromeClient() {
            override fun onGeolocationPermissionsShowPrompt(o: String, cb: GeolocationPermissions.Callback) { cb.invoke(o, true, false) }
        }

        val need = mutableListOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION)
        if (Build.VERSION.SDK_INT >= 33) need.add(Manifest.permission.POST_NOTIFICATIONS)
        if (need.any { !granted(it) }) requestPermissions(need.toTypedArray(), 1)

        web.loadUrl(server())
    }

    override fun onResume() { super.onResume(); startLoc() }
    override fun onPause() { super.onPause(); try { lm.removeUpdates(ll) } catch (_: Exception) {} }

    override fun onRequestPermissionsResult(c: Int, p: Array<out String>, r: IntArray) {
        super.onRequestPermissionsResult(c, p, r); startLoc()
    }

    private fun startLoc() {
        if (!granted(Manifest.permission.ACCESS_FINE_LOCATION) && !granted(Manifest.permission.ACCESS_COARSE_LOCATION)) return
        try {
            for (p in listOf(LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER)) {
                if (lm.isProviderEnabled(p)) {
                    lm.requestLocationUpdates(p, 3000L, 0f, ll)
                    if (loc == null) loc = lm.getLastKnownLocation(p)
                }
            }
        } catch (_: SecurityException) {}
    }

    private fun push(text: String) {
        if (Build.VERSION.SDK_INT >= 33 && !granted(Manifest.permission.POST_NOTIFICATIONS)) return
        val pi = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val n = Notification.Builder(this, "sos").setSmallIcon(android.R.drawable.ic_dialog_alert)
            .setContentTitle("CareLink").setContentText(text).setAutoCancel(true).setContentIntent(pi).build()
        getSystemService(NotificationManager::class.java).notify(nid++, n)
    }

    private fun askServer(msg: String) {
        val box = EditText(this); box.setText(server()); box.setSingleLine()
        AlertDialog.Builder(this).setTitle("Server settings").setMessage(msg + "\n\nEmulator: http://10.0.2.2:5000\nReal phone: http://<PC-IP>:5000")
            .setView(box)
            .setPositiveButton("Save and retry") { _, _ ->
                val u = box.text.toString().trim().trimEnd('/')
                if (u.startsWith("http")) { getSharedPreferences("c", 0).edit().putString("url", u).apply(); web.loadUrl(u) }
                else Toast.makeText(this, "Address must start with http", Toast.LENGTH_LONG).show()
            }.setNegativeButton("Cancel", null).show()
    }

    @Deprecated("back handling")
    override fun onBackPressed() { if (web.canGoBack()) web.goBack() else super.onBackPressed() }
}
