package com.flagdizero.jenny

import android.content.Context
import android.util.Base64
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.Security
import org.bouncycastle.crypto.params.Ed25519PrivateKeyParameters
import org.bouncycastle.crypto.signers.Ed25519Signer
import org.bouncycastle.jce.provider.BouncyCastleProvider
import org.json.JSONObject

@Suppress("UNUSED_PARAMETER")
class NodeCryptoBridge(context: Context) {
    init {
        // Il provider completo resta in coda per non sostituire TLS di Android.
        synchronized(NodeCryptoBridge::class.java) {
            val existing = Security.getProvider("BC")
            if (existing !is BouncyCastleProvider) {
                if (existing != null) Security.removeProvider("BC")
                Security.addProvider(BouncyCastleProvider())
            }
        }
    }

    private fun encode(bytes: ByteArray): String =
        Base64.encodeToString(bytes, Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING)

    fun generateIdentity(): String = try {
        val privateKey = Ed25519PrivateKeyParameters(SecureRandom())
        val publicKey = privateKey.generatePublicKey().encoded
        JSONObject().put("ok", true)
            .put("deviceId", MessageDigest.getInstance("SHA-256").digest(publicKey)
                .joinToString("") { "%02x".format(it.toInt() and 0xff) })
            .put("publicKey", encode(publicKey))
            .put("privateKey", encode(privateKey.encoded)).toString()
    } catch (e: Exception) {
        JSONObject().put("ok", false).put("error", "identity_failed").toString()
    }

    // Serve a ricostruire la chiave pubblica dal seed persistito in Python.
    fun publicKey(privateKey: String): String = try {
        encode(Ed25519PrivateKeyParameters(Base64.decode(privateKey, Base64.URL_SAFE or Base64.NO_PADDING), 0)
            .generatePublicKey().encoded)
    } catch (e: Exception) {
        JSONObject().put("ok", false).put("error", "invalid_identity").toString()
    }

    fun sign(payload: String, privateKey: String): String = try {
        val seed = Base64.decode(privateKey, Base64.URL_SAFE or Base64.NO_PADDING)
        require(seed.size == 32)
        val signer = Ed25519Signer()
        signer.init(true, Ed25519PrivateKeyParameters(seed, 0))
        val bytes = payload.toByteArray(Charsets.UTF_8)
        signer.update(bytes, 0, bytes.size)
        encode(signer.generateSignature())
    } catch (e: Exception) {
        JSONObject().put("ok", false).put("error", "sign_failed").toString()
    }
}
