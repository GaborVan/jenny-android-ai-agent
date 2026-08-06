package com.flagdizero.jenny

import android.content.Context
import android.util.AttributeSet
import android.util.SparseArray
import android.view.ViewStructure
import android.view.autofill.AutofillValue
import android.webkit.WebView

/**
 * The WebView that hosts Jenny's UI, with autofill silenced at the source.
 *
 * There is no login anywhere behind this WebView. What the user types into it is
 * configuration: API keys, a Telegram token, a backup passphrase, the password
 * of their own SSH server. The system's password manager had no way to know
 * that, so it read the page as a sign-in form — it offered to save the API key
 * into a cloud account, and from then on proposed that credential on every
 * other field served from `127.0.0.1`, an origin every localhost WebView on the
 * phone shares.
 *
 * Two things were tried first and are recorded here so nobody spends the
 * afternoon again. `autocomplete="new-password"` in the HTML is a hint the
 * autofill framework is free to ignore, and it ignores it. Setting
 * [android.view.View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS] on this
 * view does not work either: on this device `dumpsys autofill` still showed
 * fill requests arriving for our virtual children, because recent Android puts
 * every view into the assist structure regardless of that flag, and because the
 * virtual-view entry point (`notifyViewEntered(view, virtualId, bounds)`) never
 * consults it in the first place. Both are requests to be left alone. Neither
 * is a decision.
 *
 * The decision lives here. The autofill service can only see what the view
 * hands it, and a `WebView` hands over the page through the two methods below:
 * one describes the frame (including the web domain that gets matched against
 * saved credentials), the other enumerates the form fields. Overridden empty,
 * they describe nothing — so there is no domain to match, no field to classify
 * as "the password for this site", and nothing to suggest or to offer to save.
 * A flag can be overruled by a device-config change; an empty structure cannot.
 *
 * [autofill] is closed for the same reason from the other direction: even if a
 * value were somehow returned for this view, nothing writes it into the page.
 */
class NoAutofillWebView : WebView {
    constructor(context: Context) : super(context)
    constructor(context: Context, attrs: AttributeSet?) : super(context, attrs)
    constructor(context: Context, attrs: AttributeSet?, defStyleAttr: Int) :
        super(context, attrs, defStyleAttr)

    override fun onProvideAutofillStructure(structure: ViewStructure?, flags: Int) {
        // Deliberately empty: no super call, so no web domain is reported.
    }

    override fun onProvideAutofillVirtualStructure(structure: ViewStructure?, flags: Int) {
        // Deliberately empty: no super call, so no form field is reported.
    }

    override fun autofill(values: SparseArray<AutofillValue>) {
        // Deliberately empty: nothing gets filled into the page from outside.
    }
}
