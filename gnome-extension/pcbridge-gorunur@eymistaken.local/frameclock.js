/* Bir işi kare başına EN FAZLA BİR KEZ yaptırır.
 *
 * NEDEN VAR (ölçüldü 2026-09-13): fiziksel fare hareket halinde ~1000 Hz
 * rapor ediyor (medyan aralık 1,00 ms). İmleç katmanının ilk hâli aktörü
 * `position-invalidated` sinyalinin HER gelişinde taşıyordu, yani saniyede
 * bin kez konum değiştiriyordu. Ekranda görünebilecek en fazla değişiklik
 * kare sayısı kadar (~60/sn); aradaki 940 hareket kimseye bir şey
 * göstermiyor ama kabuğa iş çıkarıyor.
 *
 * İmleç katmanı gerçek makinede fiziksel fareyle tıklamayı bozmuştu ve
 * sebebi bulunamamıştı; bütün denemeler SENTETİK fareyle (~50 olay/sn)
 * yapıldığı için tek ölçülmemiş fark olay hızıydı. Bu modül o farkı
 * ortadan kaldırıyor.
 *
 * Kabuk modülü İÇE AKTARMIYOR: zamanlayıcı dışarıdan veriliyor, böylece
 * mantık `gjs` ile kabuk olmadan sınanabiliyor (`tests/test_cursor.js`).
 * Gerçek zamanlayıcı `Meta.Laters` ile kareden önce çalışan bir iş.
 */

/** Sayaçlarıyla birlikte tek bir gecikmeli iş. */
export class FrameCoalescer {
    /**
     * @param {Function} apply kare başına en fazla bir kez çağrılacak iş.
     * @param {object} options
     * @param {Function} options.schedule bir işi bir sonraki kareye kurar ve
     *   iptal için bir kimlik döndürür.
     * @param {Function} [options.cancel] kimliği verilen işi iptal eder.
     */
    constructor(apply, {schedule, cancel = () => {}} = {}) {
        this._apply = apply;
        this._schedule = schedule;
        this._cancel = cancel;
        this._pending = 0;
        this._requests = 0;
        this._applied = 0;
    }

    /** Bir olay geldi. Kare beklemede değilse yenisini kur. */
    request() {
        this._requests++;
        if (this._pending)
            return;
        this._pending = this._schedule(() => {
            // Kimlik ÖNCE sıfırlanıyor: `apply` içinden gelen yeni bir
            // `request()` bir sonraki kareye kurulabilsin. Ters sırada
            // olsaydı o istek sessizce düşerdi.
            this._pending = 0;
            this._applied++;
            this._apply();
        }) || 0;
    }

    /** Bekleyen işi iptal et. Kapanışta çağrılmazsa iş aktörsüz çalışır. */
    cancel() {
        if (!this._pending)
            return;
        try {
            this._cancel(this._pending);
        } catch {
            /* kabuk kapanıyorsa önemsiz */
        }
        this._pending = 0;
    }

    /** Kaç olay geldi, kaç kez uygulandı. Ölçüm kipinde raporlanıyor. */
    get stats() {
        return {requests: this._requests, applied: this._applied};
    }

    resetStats() {
        this._requests = 0;
        this._applied = 0;
    }
}
