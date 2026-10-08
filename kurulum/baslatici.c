/* UyapMasa.exe — derlenmiş kurulumun başlatıcısı (kurulum/uyapmasa_paketle.py derler).
 *
 * <kurulum>\python\ içinde, python314.dll'in yanında durur; gömülü Python'u başlatıp
 * derlenmiş (.pyd) uyapmasa modülünün main()'ini çağırır. sys.executable gerçek
 * python.exe'ye ayarlanır: db_baslat/uyap_app alt süreçte manage.py çalıştırıyor.
 * Pencereli alt sistem (konsol açılmaz); uyapmasa.py stdout/stderr None'ı zaten karşılar.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <windows.h>
#include <wchar.h>

static void hata(const wchar_t *mesaj)
{
    MessageBoxW(NULL, mesaj, L"UyapMasa", MB_OK | MB_ICONERROR);
}

int WINAPI wWinMain(HINSTANCE hInst, HINSTANCE hPrev, PWSTR cmd, int show)
{
    (void)hInst; (void)hPrev; (void)cmd; (void)show;
    wchar_t pydir[MAX_PATH], exe[MAX_PATH], kod[4 * MAX_PATH];
    DWORD n = GetModuleFileNameW(NULL, pydir, MAX_PATH);
    if (n == 0 || n >= MAX_PATH) { hata(L"Program yolu okunamadı."); return 1; }
    wchar_t *son = wcsrchr(pydir, L'\\');
    if (!son) { hata(L"Program yolu geçersiz."); return 1; }
    *son = L'\0';                                   /* <kurulum>\python */
    swprintf(exe, MAX_PATH, L"%ls\\python.exe", pydir);
    /* app klasörü: <kurulum>\app\UyapMasa — yol Python tarafında çözülür (Türkçe
     * karakterli kullanıcı adları için C'de ek kodlama derdi olmasın). */
    swprintf(kod, 4 * MAX_PATH,
             L"import os, sys\n"
             L"_d = os.path.join(os.path.dirname(os.path.dirname(sys.executable)), 'app', 'UyapMasa')\n"
             L"sys.path.insert(0, _d)\n"
             L"os.chdir(_d)\n"
             L"import uyapmasa\n"
             L"uyapmasa.main()\n");

    PyStatus st;
    PyConfig cfg;
    PyConfig_InitPythonConfig(&cfg);
    cfg.parse_argv = 0;
    cfg.user_site_directory = 0;   /* kullanıcının kendi Python paketleri karışmasın */
    st = PyConfig_SetString(&cfg, &cfg.home, pydir);
    if (PyStatus_Exception(st)) goto hatali;
    st = PyConfig_SetString(&cfg, &cfg.executable, exe);
    if (PyStatus_Exception(st)) goto hatali;
    st = PyConfig_SetString(&cfg, &cfg.run_command, kod);
    if (PyStatus_Exception(st)) goto hatali;

    int argc = 0;
    wchar_t **argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (argv) {
        argv[0] = exe;
        st = PyConfig_SetArgv(&cfg, argc, argv);
        LocalFree(argv);
        if (PyStatus_Exception(st)) goto hatali;
    }

    st = Py_InitializeFromConfig(&cfg);
    if (PyStatus_Exception(st)) goto hatali;
    PyConfig_Clear(&cfg);
    return Py_RunMain();

hatali:
    PyConfig_Clear(&cfg);
    hata(st.err_msg ? L"Python başlatılamadı." : L"Python başlatılamadı (bilinmeyen hata).");
    return 1;
}
