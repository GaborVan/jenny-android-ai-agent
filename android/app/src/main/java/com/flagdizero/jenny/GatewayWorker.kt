package com.flagdizero.jenny

import android.content.Context
import android.util.Log
import androidx.core.content.ContextCompat
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import java.util.concurrent.TimeUnit

/**
 * Rete di sicurezza su WorkManager: ogni 15 minuti controlla se il gateway è in
 * piedi e, se non lo è, lo rimette su.
 *
 * Perché aggiungerla al watchdog, che fa già la stessa cosa: il watchdog è una
 * catena di `AlarmManager` NOSTRA, e i firmware aggressivi hanno tutti gli
 * strumenti per smontarla — congelare l'app, revocare le sveglie esatte,
 * scartare gli alarm di un'app "non usata di recente". WorkManager gira invece
 * sul backend JobScheduler, cioè su un concetto di SISTEMA: gli OEM sono
 * marcatamente più restii a interferire con i job schedulati che con i service
 * o le sveglie di una singola app, perché lì rischiano di rompere il
 * comportamento standard della piattaforma. Non è una garanzia — è un percorso
 * *diverso*, con modalità di guasto diverse, e le reti indipendenti servono
 * proprio a questo.
 *
 * Nessun `Constraints`, di proposito: i vincoli standard (rete, carica, batteria
 * non scarica) sono pensati per lavoro *rimandabile*, e qui il lavoro è
 * "verifica di esistere". Un vincolo di rete rimanderebbe il controllo proprio
 * quando il telefono è offline — cioè in uno dei casi in cui vogliamo che il
 * gateway risalga.
 *
 * L'inizializzazione di WorkManager è quella automatica di `androidx.startup`
 * (il `WorkManagerInitializer` viene aggiunto come meta-data dell'
 * `InitializationProvider` già presente nel manifest fuso, portato da emoji2):
 * nessun `Configuration.Provider` esplicito, perché non serve nessuna
 * configurazione non-di-default e un provider custom aggiungerebbe solo un
 * punto in cui l'inizializzazione può divergere fra debug e release.
 * `JennyApplication` NON implementa `Configuration.Provider` apposta.
 */
class GatewayWorker(
    appContext: Context,
    params: WorkerParameters,
) : Worker(appContext, params) {

    /**
     * `Worker` (sincrono) e non `CoroutineWorker`: il lavoro è una lettura di
     * SharedPreferences e un `startForegroundService`, entrambi immediati.
     * Girare sul thread di WorkManager è corretto e non serve nessuno scope.
     *
     * Sempre `Result.success()`, anche quando il gateway era giù ed è stato
     * rianimato: un `retry` farebbe ripartire *questa* esecuzione con backoff,
     * mentre il lavoro periodico ha già il suo prossimo giro fra 15 minuti. Un
     * fallimento qui non è un'esecuzione da ripetere, è una constatazione da
     * riprovare al prossimo periodo.
     */
    override fun doWork(): Result {
        try {
            // `alarmFallback = true`: un worker NON gode dell'allowlist
            // temporanea che una sveglia concede, quindi su Android 12+ il suo
            // `startForegroundService` può essere rifiutato. In quel caso
            // l'unica via è rientrare da una sveglia, che l'allowlist ce l'ha.
            GatewayStarter.ensureUpIfDown(
                applicationContext,
                reason = "workmanager",
                alarmFallback = true,
            )
        } catch (e: Exception) {
            Log.e(TAG, "Periodic keepalive check failed", e)
        }
        return Result.success()
    }

    companion object {

        private const val TAG = "GatewayWorker"

        /** Nome del lavoro unico. Cambiarlo NON rinomina il lavoro già in coda:
         *  ne creerebbe un secondo accanto al primo, che continuerebbe a girare
         *  per sempre senza che nulla lo disarmi. */
        const val UNIQUE_NAME = "jenny-gateway-keepalive"

        /** Il minimo che JobScheduler accetta per un lavoro periodico
         *  (`PeriodicWorkRequest.MIN_PERIODIC_INTERVAL_MILLIS`). Chiedere meno
         *  non fa errore: WorkManager alza il valore in silenzio, e ci si
         *  ritroverebbe con un intervallo diverso da quello scritto qui. */
        private const val INTERVAL_MIN = 15L

        /**
         * Assicura che il lavoro periodico sia in coda. Idempotente, non
         * bloccante, chiamabile da qualunque thread.
         *
         * Il `getWorkInfosForUniqueWork` prima dell'enqueue non è ridondante
         * rispetto a `KEEP`: `KEEP` conserva il lavoro esistente solo se NON è
         * terminato, quindi un enqueue ripetuto su un lavoro cancellato o
         * fallito lo sostituisce — e siccome `ensureScheduled` viene chiamata a
         * ogni creazione del processo (`JennyApplication.onCreate`, che è anche
         * l'unico chiamante), senza il controllo si riazzererebbe il periodo a
         * ogni risveglio, spostando in avanti per sempre l'esecuzione
         * successiva. Il controllo rende l'arming davvero "arma se non c'è",
         * che è quello che serve.
         *
         * Il `ListenableFuture` si consuma con `addListener` e mai con `get()`:
         * il chiamante è `JennyApplication.onCreate`, che gira sul main Looper,
         * dove un `get()` bloccante sarebbe un ANR in attesa del thread interno
         * di WorkManager — per giunta nella finestra in cui il sistema misura
         * quanto ci mettiamo a partire. Dentro il listener il future è già
         * completo, quindi lì `get()` non blocca.
         */
        fun ensureScheduled(context: Context) {
            val appContext = context.applicationContext
            val manager = try {
                WorkManager.getInstance(appContext)
            } catch (e: Exception) {
                // `getInstance` solleva se l'auto-init di androidx.startup non è
                // andata a buon fine. Non è fatale: restano watchdog e sveglia
                // di sistema. Ma va detto, o il buco resterebbe invisibile.
                Log.e(TAG, "WorkManager unavailable: periodic keepalive disabled", e)
                return
            }
            val pending = manager.getWorkInfosForUniqueWork(UNIQUE_NAME)
            pending.addListener({
                try {
                    val alreadyScheduled = pending.get().any { !it.state.isFinished }
                    if (alreadyScheduled) {
                        Log.i(TAG, "Periodic keepalive already scheduled")
                        return@addListener
                    }
                    manager.enqueueUniquePeriodicWork(
                        UNIQUE_NAME,
                        ExistingPeriodicWorkPolicy.KEEP,
                        PeriodicWorkRequestBuilder<GatewayWorker>(
                            INTERVAL_MIN, TimeUnit.MINUTES
                        ).build(),
                    )
                    Log.i(TAG, "Periodic keepalive enqueued (every ${INTERVAL_MIN}min)")
                } catch (e: Exception) {
                    Log.e(TAG, "Could not schedule periodic keepalive", e)
                }
            }, ContextCompat.getMainExecutor(appContext))
        }
    }
}
