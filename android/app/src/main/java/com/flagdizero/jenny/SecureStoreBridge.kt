package com.flagdizero.jenny

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import org.json.JSONObject

class SecureStoreBridge(context: Context) {
    private val preferences = context.getSharedPreferences("jenny_secure_store", Context.MODE_PRIVATE)

    private fun key(): SecretKey = synchronized(SecureStoreBridge::class.java) {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey("jenny.openclaw.node", null) as? SecretKey) ?: run {
            val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
            generator.init(KeyGenParameterSpec.Builder(
                "jenny.openclaw.node", KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT
            ).setKeySize(256).setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
            generator.generateKey()
        }
    }

    private fun result(action: () -> JSONObject): String = try {
        action().toString()
    } catch (e: Exception) {
        JSONObject().put("ok", false).put("error", "secure_store_failed").toString()
    }

    fun putSecret(name: String, value: String): String = result {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        // Il Keystore genera un IV casuale nuovo per ogni cifratura.
        cipher.init(Cipher.ENCRYPT_MODE, key())
        check(cipher.iv.size == 12)
        val encrypted = cipher.iv + cipher.doFinal(value.toByteArray(Charsets.UTF_8))
        check(preferences.edit().putString(name, Base64.encodeToString(encrypted, Base64.NO_WRAP)).commit())
        JSONObject().put("ok", true)
    }

    fun getSecret(name: String): String = result {
        val encoded = preferences.getString(name, null)
        val value = if (encoded == null) null else {
            val encrypted = Base64.decode(encoded, Base64.NO_WRAP)
            require(encrypted.size >= 28)
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, encrypted.copyOfRange(0, 12)))
            String(cipher.doFinal(encrypted.copyOfRange(12, encrypted.size)), Charsets.UTF_8)
        }
        JSONObject().put("ok", true).put("value", value ?: JSONObject.NULL)
    }

    fun removeSecret(name: String): String = result {
        check(preferences.edit().remove(name).commit())
        JSONObject().put("ok", true)
    }
}
