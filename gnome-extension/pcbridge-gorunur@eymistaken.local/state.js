/* pcbridge'in masaüstü izin durumunu okur.
 *
 * KAYNAK: `~/.local/state/pcbridge/desktop_unlock.json`, `pcbridge/desktop/
 * safety.py` yazıyor. İçerik `{"until": <unix saniye>, "reason", "granted"}`,
 * kilitliyken `{"until": 0}`.
 *
 * NEDEN GRANT DOSYADAN: pcbridge'in güvenlik kapısı bu dosyayı zaten atomik
 * olarak yazıyor. Eklentinin dar pencere etkinleştirme D-Bus yüzü de aynı
 * gerçeği her çağrıda yeniden okur; ikinci bir izin durumu üretmez. Eklenti
 * dosyayı OKUR, asla yazmaz.
 *
 * NEDEN SÜRE HESABI BURADA: pcbridge izin süresi dolduğunda dosyayı yeniden
 * YAZMIYOR — `until` geçmişte kalıyor, o kadar. Yalnızca dosya olaylarını
 * dinleyen bir izleyici izin bittiğinde hiçbir şey duymaz ve çerçeve sonsuza
 * kadar ekranda kalırdı. Bu yüzden `until` anına ayrıca zamanlayıcı kuruluyor.
 *
 * Kabuk API'si KULLANMAZ (yalnızca GLib + Gio), böylece `gjs -m` ile kabuk
 * ayakta olmadan test edilebiliyor: `gnome-extension/tests/test_state.js`.
 */

import GLib from 'gi://GLib';
import Gio from 'gi://Gio';

/** Dosya izleyici olayları salkım salkım gelir; tek okumaya indirger. */
const DEBOUNCE_MS = 120;

/** Dosya olayı kaçarsa diye yavaş emniyet taraması. Bir `stat` + küçük okuma. */
const SWEEP_SECONDS = 30;

/** pcbridge'in izin durumunu yazdığı dosyanın olağan yeri.
 *
 * `PCBRIDGE_GORUNUR_STATE` verilmişse o kullanılır. Yalnızca test için:
 * gerçek dosyaya `{"until": ...}` yazmak pcbridge'e FİİLEN masaüstü izni
 * vermek demek — `SafetyGate` aynı dosyayı okuyor. Nested kabukta efekti
 * denerken kimseye gerçek izin vermemek gerekiyor.
 */
export function defaultStatePath() {
    const override = GLib.getenv('PCBRIDGE_GORUNUR_STATE');
    if (override)
        return override;
    return GLib.build_filenamev([
        GLib.get_user_state_dir(), 'pcbridge', 'desktop_unlock.json',
    ]);
}

/**
 * `desktop_unlock.json`'ı izler ve aktiflik değiştiğinde geri çağırır.
 *
 * @param {string} path izlenecek dosya
 * @param {(active: boolean, until: number) => void} onChange yalnızca DEĞİŞİMDE çağrılır
 */
export class UnlockState {
    constructor(path, onChange) {
        this._path = path;
        this._onChange = onChange;
        this._active = false;
        this._until = 0;
        this._monitor = null;
        this._monitorId = 0;
        this._debounceId = 0;
        this._expiryId = 0;
        this._sweepId = 0;
        this._warned = false;
    }

    get active() {
        return this._active;
    }

    get until() {
        return this._until;
    }

    /** Dosyayı şimdi yeniden oku; güvenlik sınırındaki çağrılar bunu kullanır. */
    refresh() {
        this._reread();
        return this._active;
    }

    /** İzlemeyi başlat ve mevcut durumu HEMEN yayınla.
     *
     * İlk okuma şart: eklenti izin açıkken etkinleştirilebilir (oturum açılışı,
     * `gnome-extensions enable`), o durumda çerçeve baştan görünmeli. */
    start() {
        const file = Gio.File.new_for_path(this._path);
        try {
            // WATCH_MOVES: dosya yeniden adlandırılarak değiştirilirse de duyalım.
            this._monitor = file.monitor_file(Gio.FileMonitorFlags.WATCH_MOVES, null);
            this._monitorId = this._monitor.connect('changed', () => this._schedule());
        } catch (error) {
            // İzleyici kurulamazsa emniyet taraması tek başına iş görür.
            console.warn(`[pcbridge-gorunur] dosya izleyici kurulamadı: ${error}`);
        }

        this._sweeps = 0;
        this._sweepId = GLib.timeout_add_seconds(
            GLib.PRIORITY_DEFAULT, SWEEP_SECONDS, () => {
                this._sweeps++;
                if (GLib.getenv('PCBRIDGE_GORUNUR_SELFTEST') === '1') {
                    console.log(`[pcbridge-gorunur][SELFTEST] tarama #${this._sweeps} ` +
                        `· until=${this._until} · aktif=${this._active}`);
                }
                this._reread();
                return GLib.SOURCE_CONTINUE;
            });

        this.refresh();
    }

    stop() {
        for (const alan of ['_debounceId', '_expiryId', '_sweepId']) {
            if (this[alan]) {
                GLib.Source.remove(this[alan]);
                this[alan] = 0;
            }
        }
        if (this._monitor) {
            if (this._monitorId)
                this._monitor.disconnect(this._monitorId);
            this._monitor.cancel();
            this._monitor = null;
            this._monitorId = 0;
        }
    }

    // ------------------------------------------------------------------ iç
    _schedule() {
        if (this._debounceId)
            GLib.Source.remove(this._debounceId);
        this._debounceId = GLib.timeout_add(
            GLib.PRIORITY_DEFAULT, DEBOUNCE_MS, () => {
                this._debounceId = 0;
                this._reread();
                return GLib.SOURCE_REMOVE;
            });
    }

    /** Dosyayı oku, aktifliği hesapla, değiştiyse haber ver, zamanlayıcıyı kur. */
    _reread() {
        const until = this._readUntil();
        const now = GLib.get_real_time() / 1e6;
        const active = until > now;

        this._until = until;
        this._armExpiry(until - now);

        if (active !== this._active) {
            this._active = active;
            try {
                this._onChange(active, until);
            } catch (error) {
                console.error(`[pcbridge-gorunur] durum geri çağrısı: ${error}`);
            }
        }
    }

    _readUntil() {
        let bytes;
        try {
            const [ok, contents] = GLib.file_get_contents(this._path);
            if (!ok)
                return 0;
            bytes = contents;
        } catch {
            return 0;   // dosya henüz yok — pcbridge'e hiç izin verilmemiş
        }

        try {
            const metin = new TextDecoder().decode(bytes);
            const veri = JSON.parse(metin);
            const until = Number(veri.until);
            this._warned = false;
            return Number.isFinite(until) ? until : 0;
        } catch (error) {
            // Yazımın ortasına denk gelmiş olabiliriz: bir sonraki olay düzeltir.
            // Tekrar tekrar loglamıyoruz, yoksa bozuk bir dosya journal'ı doldurur.
            if (!this._warned) {
                console.warn(`[pcbridge-gorunur] ${this._path} okunamadı: ${error}`);
                this._warned = true;
            }
            return 0;
        }
    }

    /** İzin süresi dolduğu anda yeniden değerlendirmek için zamanlayıcı. */
    _armExpiry(seconds) {
        if (this._expiryId) {
            GLib.Source.remove(this._expiryId);
            this._expiryId = 0;
        }
        if (seconds <= 0)
            return;

        // +1 sn: saniye çözünürlüklü zamanlayıcıyla `until` karşılaştırmasının
        // yarışmaması için. Erken uyanırsak aktif kalır ve yeniden kurulur.
        const ms = Math.ceil(seconds * 1000) + 1000;
        this._expiryId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
            this._expiryId = 0;
            this._reread();
            return GLib.SOURCE_REMOVE;
        });
    }
}
