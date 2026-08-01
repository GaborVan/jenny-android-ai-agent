package com.flagdizero.jenny

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.View
import android.webkit.ConsoleMessage
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.net.Uri
import org.json.JSONObject
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

/**
 * Hidden WebView bridge for agentic web search and fetch on Android.
 *
 * The WebView is a real Chrome browser instance: it executes JS, handles TLS,
 * cookies and localStorage just like the visible browser. It is kept hidden
 * (GONE) and reused across calls to amortise startup cost.
 */
class AgenticSearchBridge(context: Context) {

    companion object {
        private const val TAG = "AgenticSearchBridge"
        private const val DEFAULT_TIMEOUT_SECONDS = 30L
        private const val MAX_RESULTS_DEFAULT = 10
        private const val USER_AGENT_MOBILE =
            "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 " +
            "(KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36"

        init {
            try {
                WebView.setWebContentsDebuggingEnabled(true)
            } catch (_: Exception) {
                // safe to ignore on non-debuggable builds
            }
        }
    }

    private val handler = Handler(Looper.getMainLooper())
    private val appContext = context.applicationContext
    private var webView: WebView? = null

    private fun ensureWebView() {
        if (webView != null) return
        webView = WebView(appContext).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.databaseEnabled = true
            settings.setSupportZoom(false)
            settings.builtInZoomControls = false
            settings.displayZoomControls = false
            settings.loadsImagesAutomatically = false
            settings.mediaPlaybackRequiresUserGesture = true
            settings.javaScriptCanOpenWindowsAutomatically = false
            settings.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            settings.userAgentString = USER_AGENT_MOBILE
            visibility = View.GONE
            webChromeClient = object : WebChromeClient() {
                override fun onConsoleMessage(msg: ConsoleMessage?): Boolean {
                    Log.d(TAG, "JS console [${msg?.sourceId()}:${msg?.lineNumber()}] ${msg?.message()}")
                    return super.onConsoleMessage(msg)
                }
            }
        }
    }

    /**
     * Search Bing and return a JSON object string: { "results": [...], "pageText": "..." }.
     * Each result has: title, url, snippet. "pageText" carries the visible page
     * text only when results is empty, so the Python side can distinguish a
     * genuinely empty SERP from a bot-verification/block page.
     */
    fun searchBing(query: String, maxResults: Int, timeoutSeconds: Long = DEFAULT_TIMEOUT_SECONDS): String {
        val encoded = Uri.encode(query)
        val url = "https://www.bing.com/search?q=$encoded"
        Log.d(TAG, "searchBing ENTER: query=$query maxResults=$maxResults timeout=$timeoutSeconds")

        val js = """
            (function() {
                function collectResults(selector) {
                    const results = [];
                    document.querySelectorAll(selector).forEach(el => {
                        const linkEl = el.querySelector('a');
                        const titleEl = el.querySelector('h2, .b_title');
                        const snippetEl = el.querySelector('p, .b_caption p, .b_snippet, .b_lineclamp');
                        if (linkEl && titleEl) {
                            results.push({
                                title: titleEl.textContent.trim(),
                                url: linkEl.href,
                                snippet: snippetEl ? snippetEl.textContent.trim() : ''
                            });
                        }
                    });
                    return results;
                }
                let results = collectResults('li.b_algo');
                if (results.length === 0) {
                    results = collectResults('div.b_algo');
                }
                if (results.length === 0) {
                    results = collectResults('.b_results > li');
                }
                results = results.slice(0, ${maxResults.coerceIn(1, MAX_RESULTS_DEFAULT)});
                const pageText = results.length === 0
                    ? (document.body ? document.body.innerText.slice(0, 4000) : '')
                    : '';
                return JSON.stringify({results: results, pageText: pageText});
            })()
        """.trimIndent()

        val result = evaluateOnPage(url, js, timeoutSeconds)

        if (result.isBlank() || result == "null") {
            Log.w(TAG, "Bing search returned no data from WebView")
            return JSONObject().apply {
                put("error", "WebView returned no data for Bing search")
            }.toString()
        }

        Log.d(TAG, "Bing search response length: ${result.length}")
        return result
    }

    /**
     * Fetch any URL through the hidden WebView and return the rendered HTML.
     * Returns a JSON object: { "html": "...", "finalUrl": "..." }.
     */
    fun fetchUrl(url: String, timeoutSeconds: Long = DEFAULT_TIMEOUT_SECONDS): String {
        Log.d(TAG, "fetchUrl ENTER: url=$url timeout=$timeoutSeconds")
        val js = """
            JSON.stringify({
                html: document.documentElement.outerHTML,
                finalUrl: window.location.href
            })
        """.trimIndent()
        return evaluateOnPage(url, js, timeoutSeconds)
    }

    /**
     * Destroy the hidden WebView and release resources.
     */
    fun destroy() {
        Log.d(TAG, "Destroying bridge")
        handler.post {
            webView?.stopLoading()
            webView?.destroy()
            webView = null
        }
    }

    /**
     * Load [url], wait for page finish, then run [js] and return the result.
     * Runs on the main thread and blocks the calling thread.
     */
    private fun evaluateOnPage(url: String, js: String, timeoutSeconds: Long): String {
        Log.d(TAG, "evaluateOnPage ENTER: url=$url timeout=$timeoutSeconds")
        val latch = CountDownLatch(1)
        val ref = AtomicReference<String>("")
        val errorRef = AtomicReference<String?>(null)

        handler.post {
            Log.d(TAG, "evaluateOnPage: setting up WebViewClient and loading URL")
            ensureWebView()
            val wv = webView!!
            wv.webViewClient = object : WebViewClient() {
                override fun onReceivedError(
                    view: WebView?,
                    request: WebResourceRequest?,
                    error: WebResourceError?
                ) {
                    if (request?.isForMainFrame != false) {
                        val msg = "WebView error: ${error?.description ?: "unknown"} (${error?.errorCode ?: -1})"
                        Log.e(TAG, msg)
                        errorRef.set(msg)
                        latch.countDown()
                    }
                }

                override fun onReceivedHttpError(
                    view: WebView?,
                    request: WebResourceRequest?,
                    errorResponse: android.webkit.WebResourceResponse?
                ) {
                    if (request?.isForMainFrame != false) {
                        val status = errorResponse?.statusCode ?: -1
                        val msg = "WebView HTTP error: $status for ${request?.url ?: url}"
                        Log.e(TAG, msg)
                        errorRef.set(msg)
                        latch.countDown()
                    }
                }

                override fun onPageStarted(view: WebView?, startedUrl: String?, favicon: android.graphics.Bitmap?) {
                    Log.d(TAG, "onPageStarted: $startedUrl")
                }

                override fun onPageFinished(view: WebView?, finishedUrl: String?) {
                    Log.d(TAG, "onPageFinished: $finishedUrl (error=${errorRef.get()})")
                    if (errorRef.get() != null) {
                        latch.countDown()
                        return
                    }
                    Log.d(TAG, "evaluateOnPage: running evaluateJavascript")
                    view?.evaluateJavascript(js) { value ->
                        Log.d(TAG, "evaluateJavascript callback: value length=${value?.length ?: 0} null=${value == null}")
                        ref.set(value ?: "")
                        latch.countDown()
                    }
                }
            }
            Log.d(TAG, "evaluateOnPage: calling loadUrl($url)")
            wv.loadUrl(url)
            Log.d(TAG, "evaluateOnPage: loadUrl returned (post)")
        }

        Log.d(TAG, "evaluateOnPage: waiting on latch...")
        val completed = latch.await(timeoutSeconds, TimeUnit.SECONDS)
        Log.d(TAG, "evaluateOnPage: latch completed=$completed error=${errorRef.get()}")

        if (errorRef.get() != null) {
            handler.post { webView?.stopLoading() }
            return JSONObject().apply {
                put("error", errorRef.get())
            }.toString()
        }
        if (!completed) {
            handler.post { webView?.stopLoading() }
            val msg = "WebView timeout after ${timeoutSeconds}s for $url"
            Log.w(TAG, msg)
            return JSONObject().apply {
                put("error", msg)
            }.toString()
        }

        Log.d(TAG, "evaluateOnPage EXIT: ref length=${ref.get().length}")
        return ref.get()
    }
}
