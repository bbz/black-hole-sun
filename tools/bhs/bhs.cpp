// bhs.dll: our code inside a running Dead Star instance (REL-147994). Load it with tools/inject.py.
//
// It hooks GameComponentsNeedle's import of CGameManager::GameController, which the game calls many
// times per frame with the game manager as `this`. The game logic doesn't run on the window's
// thread, so the first thread to call the hook is taken as the game thread (and again after a match
// restart, when the game thread is replaced). At most once per frame the hook runs tick() on that
// thread, so tick() may call game functions. tick() reads commands from
// ..\bhs\cmd.txt (relative to Bin64, i.e. game/<instance>/bhs) and logs to ..\bhs\log.txt.
//
// Commands (one per line; the file is deleted once read):
//   info                  manager, controller, player count, local player id, network type
//   find VTABLE           live components whose vtable is VTABLE (a GameComponentsNeedle VA)
//   peek ADDR [N]         hex dump N (hex, default 40) bytes
//   poke8 ADDR BYTE       write one byte
//   call VA [ARG...]      call a GameComponentsNeedle function (VA at 0x180000000) with up to six
//                         arguments (hex numbers, or mgr / ctrl) and log the return value
//   ai_next [TEAM]        spectator: follow the next AI ship (of TEAM, default the watched team),
//                         preferring one that is fighting
//   ai_list               AI ships grouped by kind (component count of their game object), with
//                         the vtables of one example of each kind; with `only`, each ship of that kind
//   follow 0|1|2          spectator, every frame: 1 (default) runs the LocalProxy's own update
//                         (kProxyUpdate), which isn't registered on a listen server; 2 only copies
//                         the watched object's transform to the proxy; 0 does nothing
//   watched               spectator: what the camera follows (component, kind, position)
//   only COMPONENTS       spectator: only follow AI ships whose game object has COMPONENTS
//                         components (a kind from ai_list; 0: any)
//   director SECONDS      spectator: every SECONDS switch to an AI ship of the other team (0: off;
//                         default 8)
//   sound [VOLUME]        this process's volume in the Windows mixer, 0-100; without VOLUME, toggle
//                         mute (the game's own wwise_masterVolume doesn't reliably silence it)
//   commander SECONDS     every SECONDS (default 2; 0: off) send each ai_teams squad to the nearest
//                         capture point its team doesn't own, onto the patrol ring around it
//   push TEAM 0|1         commander: send TEAM's squad straight to the enemy's points (1) or back to
//                         the nearest point it doesn't own (0, default)
//   unload                restore the hook and unload the DLL
//
// Build: tools/bhs/build.ps1 (MinGW-w64). Only plain pointers and integers cross into game code.
#include <windows.h>
#include <audiopolicy.h>
#include <mmdeviceapi.h>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cmath>
#include <cstring>

namespace {

constexpr uintptr_t kImageBase = 0x180000000;
const char* kGameController = "?GameController@CGameManager@@QEAAPEAVCGameObjectComponentGameController@@XZ";

// GameCore globals (VA at kImageBase)
constexpr uintptr_t kNetworkType = 0x1806a6058;   // network type
constexpr uintptr_t kLocalPlayerId = 0x1806a6fea;  // local player id

// game controller
constexpr size_t kPlayerCount = 0x80;
// game manager component table: 16-byte entries (generation, component); count follows it
constexpr size_t kComponentTable = 0x40118;

// GameComponentsNeedle (VA at kImageBase)
constexpr uintptr_t kVtableAI = 0x1802beea0;          // NeedleController/AI
constexpr uintptr_t kVtableSquad = 0x1802c2390;       // NeedleAIManager/Squad
constexpr uintptr_t kVtablePatrol = 0x1802c20b0;      // NeedleWaypoint/PatrolPoint
// Squad: members, leader (AI controller id) +0xc8, current patrol point id +0x100
constexpr size_t kSquadLeader = 0xc8, kSquadPatrolPoint = 0x100;
constexpr size_t kSquadMembers = 0xa8;  // begin, end of 16-byte member ids
constexpr uintptr_t kSquadSetPatrolPoint = 0x1801297c0;  // (squad, point): clears it if it's the current one
// PatrolPoint: next point id +0x98 (previous +0xa8)
constexpr size_t kPatrolNext = 0x98;
constexpr uintptr_t kVtableLocalProxy = 0x1802bd740;  // NeedleShip/LocalProxy (spectator camera)
constexpr uintptr_t kObserverNext = 0x1800f9cd0;      // (proxy, manager): follow the team's next ship
constexpr uintptr_t kProxyUpdate = 0x1800fa050;       // (proxy, manager, dt): LocalProxy per-frame update
constexpr size_t kAIAction = 0x244;  // 0 inactive, 1 idle, 2 patrol, 3 follow leader, 4 chase
// LocalProxy spectator state: watched team (0-3, 0xff none), watched component id, watched player
constexpr size_t kWatchTeam = 0xa0, kWatchComponent = 0xa8, kWatchPlayer = 0xb8;

using GameControllerFn = void* (*)(void* manager);
using GetTransformFn = const void* (*)(const void* component);  // CMatrix34 const&
using SetTransformFn = void (*)(void* component, const void* matrix);
GetTransformFn g_get_transform;
SetTransformFn g_set_transform;
// CVector3 returned through a hidden pointer (MSVC x64 member function returning a class)
using GetVelocityFn = float* (*)(const void* component, float* out);
GetVelocityFn g_get_velocity;
int g_follow = 1;

HMODULE g_self;
uintptr_t g_core, g_needle;
size_t g_needle_size;  // image size, to tell its vtables
void** g_iat_slot;
GameControllerFn g_original;
volatile DWORD g_game_thread;
volatile LONGLONG g_game_thread_seen;  // QPC time of its last call
volatile LONG g_in_tick;
volatile bool g_unloading;
LARGE_INTEGER g_freq, g_last, g_director_last;
PVOID g_float_handler;
int g_only_kind;  // ai_next follows only AI ships with this many components (0: any)
double g_commander_period = 2;  // seconds, 0 = off
bool g_push[2];                  // per team: target only points the other team owns
LARGE_INTEGER g_commander_last;
double g_director_period = 8;  // seconds, 0 = off
char g_dir[MAX_PATH];

uintptr_t core(uintptr_t va) { return g_core + (va - kImageBase); }
uintptr_t needle(uintptr_t va) { return g_needle + (va - kImageBase); }

void log(const char* fmt, ...) {
    char path[MAX_PATH];
    snprintf(path, sizeof path, "%s\\log.txt", g_dir);
    FILE* f = fopen(path, "a");
    if (!f) return;
    SYSTEMTIME t;
    GetLocalTime(&t);
    fprintf(f, "%02d:%02d:%02d.%03d ", t.wHour, t.wMinute, t.wSecond, t.wMilliseconds);
    va_list a;
    va_start(a, fmt);
    vfprintf(f, fmt, a);
    va_end(a);
    fputc('\n', f);
    fclose(f);
}

void** find_iat_slot(uintptr_t module, const char* dll, const char* name) {
    auto dos = reinterpret_cast<IMAGE_DOS_HEADER*>(module);
    auto nt = reinterpret_cast<IMAGE_NT_HEADERS64*>(module + dos->e_lfanew);
    auto& dir = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    for (auto imp = reinterpret_cast<IMAGE_IMPORT_DESCRIPTOR*>(module + dir.VirtualAddress); imp->Name; ++imp) {
        if (_stricmp(reinterpret_cast<char*>(module + imp->Name), dll) != 0) continue;
        auto names = reinterpret_cast<IMAGE_THUNK_DATA64*>(module + imp->OriginalFirstThunk);
        auto slots = reinterpret_cast<IMAGE_THUNK_DATA64*>(module + imp->FirstThunk);
        for (; names->u1.AddressOfData; ++names, ++slots) {
            if (IMAGE_SNAP_BY_ORDINAL64(names->u1.Ordinal)) continue;
            auto by_name = reinterpret_cast<IMAGE_IMPORT_BY_NAME*>(module + names->u1.AddressOfData);
            if (strcmp(reinterpret_cast<char*>(by_name->Name), name) == 0)
                return reinterpret_cast<void**>(&slots->u1.Function);
        }
    }
    return nullptr;
}

void set_slot(void* value) {
    DWORD old;
    VirtualProtect(g_iat_slot, sizeof(void*), PAGE_READWRITE, &old);
    *g_iat_slot = value;
    VirtualProtect(g_iat_slot, sizeof(void*), old, &old);
}

DWORD WINAPI unload_thread(LPVOID) {
    Sleep(500);  // let any call still inside our hook return
    FreeLibraryAndExitThread(g_self, 0);
}

bool readable(const void* p, size_t n) {
    MEMORY_BASIC_INFORMATION mbi;
    if (!VirtualQuery(p, &mbi, sizeof mbi) || mbi.State != MEM_COMMIT) return false;
    if (mbi.Protect & (PAGE_NOACCESS | PAGE_GUARD)) return false;
    return static_cast<const char*>(p) + n <= static_cast<const char*>(mbi.BaseAddress) + mbi.RegionSize;
}

uintptr_t arg(void* manager, const char* t) {
    if (strcmp(t, "mgr") == 0) return reinterpret_cast<uintptr_t>(manager);
    if (strcmp(t, "ctrl") == 0) return reinterpret_cast<uintptr_t>(g_original(manager));
    return strtoull(t, nullptr, 16);
}

void find(void* manager, const char* vtable) {
    uintptr_t vt = needle(strtoull(vtable, nullptr, 16));
    auto base = static_cast<uint8_t*>(manager);
    auto table = *reinterpret_cast<uint8_t**>(base + kComponentTable);
    auto count = *reinterpret_cast<unsigned long long*>(base + kComponentTable + 8);
    int found = 0;
    for (unsigned long long i = 0; i < count; ++i) {
        auto comp = *reinterpret_cast<uintptr_t**>(table + i * 16 + 8);
        if (comp && *comp == vt) {
            log("  [%llx] %p", i, comp);
            ++found;
        }
    }
    log("find %s: %d of %llu components", vtable, found, count);
}

void peek(const uint8_t* p, size_t len) {
    if (!readable(p, len)) {
        log("peek %p: not readable", p);
        return;
    }
    for (size_t o = 0; o < len; o += 16) {
        char hex[40] = {}, *h = hex;
        for (size_t k = o; k < o + 16 && k < len; ++k) h += sprintf(h, "%02x", p[k]);
        log("  %p +%03zx %s", p + o, o, hex);
    }
}

// Component for a 16-byte unique id (index, generation, tag), or null.
uint8_t* component(void* manager, const uint8_t* id) {
    auto base = static_cast<uint8_t*>(manager);
    auto table = *reinterpret_cast<uint8_t**>(base + kComponentTable);
    auto count = *reinterpret_cast<unsigned long long*>(base + kComponentTable + 8);
    uint32_t index = *reinterpret_cast<const uint32_t*>(id), gen = *reinterpret_cast<const uint32_t*>(id + 4);
    if (index >= count || *reinterpret_cast<uint32_t*>(table + index * 16) != gen) return nullptr;
    return *reinterpret_cast<uint8_t**>(table + index * 16 + 8);
}

template <class F> void each_component(void* manager, uintptr_t vtable, F f) {
    auto base = static_cast<uint8_t*>(manager);
    auto table = *reinterpret_cast<uint8_t**>(base + kComponentTable);
    auto count = *reinterpret_cast<unsigned long long*>(base + kComponentTable + 8);
    uintptr_t vt = needle(vtable);
    for (unsigned long long i = 0; i < count; ++i) {
        auto comp = *reinterpret_cast<uintptr_t**>(table + i * 16 + 8);
        if (comp && *comp == vt) f(reinterpret_cast<uint8_t*>(comp));
    }
}

// Components whose game object (+0x10) is `object`.
template <class F> void components_of(void* manager, void* object, F f) {
    auto base = static_cast<uint8_t*>(manager);
    auto table = *reinterpret_cast<uint8_t**>(base + kComponentTable);
    auto count = *reinterpret_cast<unsigned long long*>(base + kComponentTable + 8);
    for (unsigned long long i = 0; i < count; ++i) {
        auto comp = *reinterpret_cast<uint8_t**>(table + i * 16 + 8);
        if (comp && *reinterpret_cast<void**>(comp + 0x10) == object) f(comp);
    }
}

// How worth watching an AI ship is, from its action: fighting, then moving, then idle.
int interest(int action) { return action == 4 ? 2 : (action == 2 || action == 3) ? 1 : 0; }

// Spectator: step through the watched team's ships until one is flown by the AI, taking the most
// interesting kind available. A ship's game object (component +0x10) is AI-flown if it also has a
// NeedleController/AI component. Returns the action of the ship followed, or -1.
int ai_next(void* manager, int team) {
    uint8_t* proxy = nullptr;
    each_component(manager, kVtableLocalProxy, [&](uint8_t* c) { proxy = c; });
    if (!proxy) return -1;
    struct Ai { void* object; int action; } ai[512];
    int n = 0;
    each_component(manager, kVtableAI, [&](uint8_t* c) {
        void* object = *reinterpret_cast<void**>(c + 0x10);
        if (g_only_kind) {
            int k = 0;
            components_of(manager, object, [&](uint8_t*) { ++k; });
            if (k != g_only_kind) return;
        }
        if (n < 512) ai[n++] = {object, *reinterpret_cast<int*>(c + kAIAction)};
    });
    if (!n) return -1;
    if (team >= 0) proxy[kWatchTeam] = static_cast<uint8_t>(team);
    proxy[kWatchPlayer] = 0xff;
    auto next = reinterpret_cast<void (*)(void*, void*)>(needle(kObserverNext));
    for (int want = 2; want >= 0; --want) {
        for (int step = 0; step < 256; ++step) {
            next(proxy, manager);
            uint8_t* watched = component(manager, proxy + kWatchComponent);
            if (!watched) return -1;  // team has no ships
            void* object = *reinterpret_cast<void**>(watched + 0x10);
            for (int i = 0; i < n; ++i)
                if (ai[i].object == object && interest(ai[i].action) >= want) return ai[i].action;
        }
    }
    return -1;
}

uint32_t crc32(const char* s) {
    uint32_t c = 0xffffffff;
    for (; *s; ++s) {
        c ^= static_cast<uint8_t>(*s);
        for (int k = 0; k < 8; ++k) c = (c >> 1) ^ (0xedb88320 & (0 - (c & 1)));
    }
    return ~c;
}

// A component's type and subtype name hashes (e.g. NeedleWaypoint / CapturePoint) are the first two
// ints of the type info at *(*(component + 0x28) + 0x60).
bool is_type(const uint8_t* c, uint32_t type, uint32_t subtype) {
    auto info = *reinterpret_cast<uint32_t* const*>(*reinterpret_cast<uint8_t* const*>(c + 0x28) + 0x60);
    return info[0] == type && info[1] == subtype;
}

template <class F> void each_of_type(void* manager, uint32_t type, uint32_t subtype, F f) {
    auto base = static_cast<uint8_t*>(manager);
    auto table = *reinterpret_cast<uint8_t**>(base + kComponentTable);
    auto count = *reinterpret_cast<unsigned long long*>(base + kComponentTable + 8);
    for (unsigned long long i = 0; i < count; ++i) {
        auto comp = *reinterpret_cast<uint8_t**>(table + i * 16 + 8);
        // free slots can still point at freed components: trust only ones with one of our module's
        // vtables whose own id (+0x18: index, generation) is this slot's
        if (!comp || *reinterpret_cast<uintptr_t*>(comp) - g_needle >= g_needle_size) continue;
        auto id = reinterpret_cast<const uint32_t*>(comp + 0x18);
        if (id[0] != i || id[1] != *reinterpret_cast<uint32_t*>(table + i * 16)) continue;
        if (is_type(comp, type, subtype)) f(comp);
    }
}


// Team of a component through its owner (component +0x98 for capture points, +0x80 for AI
// controllers; the owner's team byte is at +0xb1), or -1.
int team_at(const uint8_t* c, size_t owner) {
    auto o = *reinterpret_cast<uint8_t* const*>(c + owner);
    return o ? static_cast<int8_t>(o[0xb1]) : -1;
}

const float* position(const uint8_t* c) { return static_cast<const float*>(g_get_transform(c)) + 9; }

// Up to `max` points of the ring that `start` is on, following next links.
int ring_of(void* manager, uint8_t* start, uint8_t** ring, int max) {
    int n = 0;
    for (uint8_t* p = start; p && n < max; p = component(manager, p + kPatrolNext)) {
        for (int i = 0; i < n; ++i)
            if (ring[i] == p) return n;
        ring[n++] = p;
    }
    return n;
}

void ai_list(void* manager) {
    struct Kind { int components, ships; uint8_t* example; } kinds[32];
    int n = 0;
    each_component(manager, kVtableAI, [&](uint8_t* c) {
        void* object = *reinterpret_cast<void**>(c + 0x10);
        int k = 0;
        components_of(manager, object, [&](uint8_t*) { ++k; });
        int i = 0;
        while (i < n && kinds[i].components != k) ++i;
        if (i == n && n < 32) {
            kinds[n++] = {k, 0, c};
            auto props = *reinterpret_cast<float**>(c + 0x100);
            log("ai_list: kind %d: radius %.1f flag1b8 %d weights desired %.2f object %.2f ship %.2f aggro range %.0f",
                k, *reinterpret_cast<float*>(c + 0xdc), c[0x1b8], props[0xcc / 4], props[0xd0 / 4], props[0xd4 / 4],
                props[0x98 / 4]);
        }
        if (i < n) ++kinds[i].ships;
        if (k == g_only_kind) {
            auto obj = *reinterpret_cast<uint8_t**>(static_cast<uint8_t*>(object) + 0x1b8);
            auto m = reinterpret_cast<const float*>(obj + 0x90);
            float v[3] = {};
            if (g_get_velocity) g_get_velocity(c, v);
            log("ai_list: AI %p at (%.0f, %.0f) action %d team %d speed %.0f", c, m[9], m[11], *reinterpret_cast<int*>(c + kAIAction),
                team_at(c, 0x80), sqrtf(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]));
        }
    });
    for (int i = 0; i < n; ++i) {
        char vts[1024] = {}, *v = vts;
        components_of(manager, *reinterpret_cast<void**>(kinds[i].example + 0x10), [&](uint8_t* c) {
            if (v < vts + sizeof vts - 16)
                v += sprintf(v, " %llx", static_cast<unsigned long long>(kImageBase + (*reinterpret_cast<uintptr_t*>(c) - g_needle)));
        });
        log("ai_list: %d ships with %d components, e.g. AI %p:%s", kinds[i].ships, kinds[i].components,
            kinds[i].example, vts);
    }
}

// Set this process's audio sessions (its entry in the Windows volume mixer), on every output
// device: volume 0-100, or -1 to toggle mute. Runs on its own thread so it can set up COM there
// without touching the game's threads.
DWORD WINAPI sound_thread(LPVOID param) {
    int volume = static_cast<int>(reinterpret_cast<intptr_t>(param));
    CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    IMMDeviceEnumerator* devices = nullptr;
    IMMDeviceCollection* outputs = nullptr;
    UINT count = 0;
    int sessions = 0;
    if (SUCCEEDED(CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL, __uuidof(IMMDeviceEnumerator),
                                   reinterpret_cast<void**>(&devices))) &&
        SUCCEEDED(devices->EnumAudioEndpoints(eRender, DEVICE_STATE_ACTIVE, &outputs)))
        outputs->GetCount(&count);
    for (UINT d = 0; d < count; ++d) {
        IMMDevice* device = nullptr;
        IAudioSessionManager2* manager = nullptr;
        IAudioSessionEnumerator* list = nullptr;
        int n = 0;
        if (SUCCEEDED(outputs->Item(d, &device)) &&
            SUCCEEDED(device->Activate(__uuidof(IAudioSessionManager2), CLSCTX_ALL, nullptr,
                                       reinterpret_cast<void**>(&manager))) &&
            SUCCEEDED(manager->GetSessionEnumerator(&list)))
            list->GetCount(&n);
        for (int i = 0; i < n; ++i) {
            IAudioSessionControl* control = nullptr;
            IAudioSessionControl2* control2 = nullptr;
            ISimpleAudioVolume* level = nullptr;
            DWORD pid = 0;
            if (SUCCEEDED(list->GetSession(i, &control)) &&
                SUCCEEDED(control->QueryInterface(__uuidof(IAudioSessionControl2), reinterpret_cast<void**>(&control2))) &&
                SUCCEEDED(control2->GetProcessId(&pid)) && pid == GetCurrentProcessId() &&
                SUCCEEDED(control->QueryInterface(__uuidof(ISimpleAudioVolume), reinterpret_cast<void**>(&level)))) {
                BOOL muted = FALSE;
                float before = 0;
                level->GetMute(&muted);
                level->GetMasterVolume(&before);
                if (volume < 0) {
                    level->SetMute(!muted, nullptr);
                } else {
                    level->SetMasterVolume(volume / 100.0f, nullptr);
                    level->SetMute(volume == 0, nullptr);
                }
                BOOL now_muted = FALSE;
                float after = 0;
                level->GetMute(&now_muted);
                level->GetMasterVolume(&after);
                log("sound: %s %.0f%% -> %s %.0f%%", muted ? "muted" : "on", before * 100,
                    now_muted ? "muted" : "on", after * 100);
                ++sessions;
            }
            if (level) level->Release();
            if (control2) control2->Release();
            if (control) control->Release();
        }
        if (list) list->Release();
        if (manager) manager->Release();
        if (device) device->Release();
    }
    if (!sessions) log("sound: no audio session for this process");
    if (outputs) outputs->Release();
    if (devices) devices->Release();
    CoUninitialize();
    return 0;
}

void command(void* manager, char* line) {
    char* tok[8] = {};
    int n = 0;
    for (char* t = strtok(line, " "); t && n < 8; t = strtok(nullptr, " ")) tok[n++] = t;
    if (!n) return;
    const char* cmd = tok[0];
    if (strcmp(cmd, "info") == 0) {
        auto ctrl = static_cast<uint8_t*>(g_original(manager));
        log("thread %lu manager %p controller %p players %llu local player %u network type %d", g_game_thread,
            manager, ctrl, ctrl ? *reinterpret_cast<unsigned long long*>(ctrl + kPlayerCount) : 0ull,
            *reinterpret_cast<uint8_t*>(core(kLocalPlayerId)), *reinterpret_cast<int*>(core(kNetworkType)));
    } else if (strcmp(cmd, "find") == 0 && n == 2) {
        find(manager, tok[1]);
    } else if (strcmp(cmd, "peek") == 0 && n >= 2) {
        peek(reinterpret_cast<uint8_t*>(arg(manager, tok[1])), n > 2 ? strtoull(tok[2], nullptr, 16) : 0x40);
    } else if (strcmp(cmd, "poke8") == 0 && n == 3) {
        auto p = reinterpret_cast<uint8_t*>(arg(manager, tok[1]));
        if (!readable(p, 1)) {
            log("poke8 %p: not readable", p);
            return;
        }
        *p = static_cast<uint8_t>(strtoul(tok[2], nullptr, 16));
        log("poke8 %p = %02x", p, *p);
    } else if (strcmp(cmd, "call") == 0 && n >= 2) {
        using Fn = uintptr_t (*)(uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t);
        uintptr_t a[6] = {};
        for (int i = 2; i < n; ++i) a[i - 2] = arg(manager, tok[i]);
        auto fn = reinterpret_cast<Fn>(needle(strtoull(tok[1], nullptr, 16)));
        uintptr_t r = fn(a[0], a[1], a[2], a[3], a[4], a[5]);
        log("call %s -> %llx", tok[1], static_cast<unsigned long long>(r));
    } else if (strcmp(cmd, "ai_next") == 0) {
        int action = ai_next(manager, n > 1 ? atoi(tok[1]) : -1);
        log("ai_next: %s (action %d)", action >= 0 ? "following an AI ship" : "no AI ship found", action);
    } else if (strcmp(cmd, "watched") == 0) {
        uint8_t* proxy = nullptr;
        each_component(manager, kVtableLocalProxy, [&](uint8_t* c) { proxy = c; });
        uint8_t* w = proxy ? component(manager, proxy + kWatchComponent) : nullptr;
        if (!w) {
            log("watched: nothing (proxy %p)", proxy);
            return;
        }
        void* object = *reinterpret_cast<void**>(w + 0x10);
        int k = 0;
        components_of(manager, object, [&](uint8_t*) { ++k; });
        auto m = static_cast<const float*>(g_get_transform(w));
        auto p = static_cast<const float*>(g_get_transform(proxy));
        log("watched: team %d component %p (vtable %llx), %d components, at (%.0f, %.0f); proxy at (%.0f, %.0f)",
            proxy[kWatchTeam], w, static_cast<unsigned long long>(kImageBase + (*reinterpret_cast<uintptr_t*>(w) - g_needle)),
            k, m[9], m[11], p[9], p[11]);
    } else if (strcmp(cmd, "push") == 0 && n == 3 && (tok[1][0] == '0' || tok[1][0] == '1')) {
        g_push[tok[1][0] - '0'] = atoi(tok[2]) != 0;
        log("push: team %c %s", tok[1][0], g_push[tok[1][0] - '0'] ? "on" : "off");
    } else if (strcmp(cmd, "commander") == 0 && n == 2) {
        g_commander_period = atof(tok[1]);
        log("commander every %.1f s", g_commander_period);
    } else if (strcmp(cmd, "rings") == 0) {
        each_component(manager, kVtablePatrol, [&](uint8_t* p) {
            uint8_t* ring[16];
            int n = ring_of(manager, p, ring, 16);
            for (int i = 1; i < n; ++i) if (ring[i] < p) return;  // print each ring once
            char text[600] = {}, *t = text;
            for (int i = 0; i < n && t < text + 560; ++i) {
                const float* q = position(ring[i]);
                t += sprintf(t, " (%.0f, %.0f)", q[0], q[2]);
            }
            log("ring %p, %d points%s%s", p, n, text, component(manager, ring[n - 1] + kPatrolNext) ? "" : " (open)");
        });
    } else if (strcmp(cmd, "points") == 0) {
        each_of_type(manager, crc32("NeedleWaypoint"), crc32("CapturePoint"), [&](uint8_t* c) {
            const float* q = position(c);
            log("capture point %p at (%.0f, %.0f) team %d", c, q[0], q[2], team_at(c, 0x98));
        });
    } else if (strcmp(cmd, "detect") == 0) {
        // each AI ship's NeedleScannable/Generic: whether teams 0-3 detect it (+0xc8 + team * 4)
        static const uint32_t kScannable = crc32("NeedleScannable"), kGeneric = crc32("Generic");
        each_component(manager, kVtableAI, [&](uint8_t* c) {
            void* object = *reinterpret_cast<void**>(c + 0x10);
            int k = 0;
            uint8_t* scan = nullptr;
            components_of(manager, object, [&](uint8_t* x) {
                ++k;
                if (is_type(x, kScannable, kGeneric)) scan = x;
            });
            if (!scan) {
                log("detect: AI %p kind %d team %d: no scannable", c, k, team_at(c, 0x80));
                return;
            }
            auto d = reinterpret_cast<const int*>(scan + 0xc8);
            log("detect: AI %p kind %d team %d flag %d seen by %d %d %d %d", c, k, team_at(c, 0x80), scan[0x7e], d[0], d[1], d[2], d[3]);
        });
    } else if (strcmp(cmd, "scanners") == 0) {
        // ship scanners (vtable 0x1802c18a0): owner +0xb0, period +0xa4, timer +0xa8,
        // range props(+0x80)+0x78 plus bonus +0x98
        each_component(manager, 0x1802c18a0, [&](uint8_t* c) {
            void* object = *reinterpret_cast<void**>(c + 0x10);
            int k = 0;
            components_of(manager, object, [&](uint8_t*) { ++k; });
            auto props = *reinterpret_cast<uint8_t**>(c + 0x80);
            log("scanner %p kind %d owner %p team %d period %.2f timer %.2f range %.0f+%.0f", c, k,
                *reinterpret_cast<void**>(c + 0xb0), team_at(c, 0xb0), *reinterpret_cast<float*>(c + 0xa4),
                *reinterpret_cast<float*>(c + 0xa8), props ? *reinterpret_cast<float*>(props + 0x78) : -1.f,
                *reinterpret_cast<float*>(c + 0x98));
        });
    } else if (strcmp(cmd, "targets") == 0) {
        // what each team's AI ships are attacking (AI +0x150): a structure, another ship, nothing
        static const uint32_t kStructure = crc32("NeedleStructure"), kShip = crc32("NeedleShip");
        int count[4][3] = {};
        each_component(manager, kVtableAI, [&](uint8_t* c) {
            int team = team_at(c, 0x80);
            if (team < 0 || team > 3) return;
            uint8_t* t = component(manager, c + 0x150);
            int kind = 2;
            if (t) {
                components_of(manager, *reinterpret_cast<void**>(t + 0x10), [&](uint8_t* x) {
                    auto info = *reinterpret_cast<uint32_t* const*>(*reinterpret_cast<uint8_t* const*>(x + 0x28) + 0x60);
                    if (info[0] == kStructure) kind = 0;
                    else if (info[0] == kShip && kind != 0) kind = 1;
                });
            }
            ++count[team][kind];
        });
        for (int t = 0; t < 4; ++t)
            if (count[t][0] + count[t][1] + count[t][2])
                log("targets: team %d: %d on structures, %d on ships, %d none", t, count[t][0], count[t][1], count[t][2]);
    } else if (strcmp(cmd, "squads") == 0) {
        each_component(manager, kVtableSquad, [&](uint8_t* sq) {
            uint8_t* leader = component(manager, sq + kSquadLeader);
            uint8_t* point = component(manager, sq + kSquadPatrolPoint);
            auto first = *reinterpret_cast<uint8_t**>(sq + kSquadMembers), last = *reinterpret_cast<uint8_t**>(sq + kSquadMembers + 8);
            char members[512] = {}, *m = members;
            for (uint8_t* id = first; id && id < last && m < members + 400; id += 16) {
                uint8_t* c = component(manager, id);
                m += sprintf(m, " %p/%llx/t%d", c, c ? static_cast<unsigned long long>(kImageBase + (*reinterpret_cast<uintptr_t*>(c) - g_needle)) : 0ull,
                             c ? team_at(c, 0x80) : -9);
            }
            const float* lp = leader ? position(leader) : nullptr;
            const float* pp = point ? position(point) : nullptr;
            log("squad %p leader %p (%.0f, %.0f) point (%.0f, %.0f) members:%s", sq, leader, lp ? lp[0] : 0.f, lp ? lp[2] : 0.f,
                pp ? pp[0] : 0.f, pp ? pp[2] : 0.f, members);
        });
    } else if (strcmp(cmd, "only") == 0 && n == 2) {
        g_only_kind = atoi(tok[1]);
        log("only AI ships with %d components", g_only_kind);
    } else if (strcmp(cmd, "ai_list") == 0) {
        ai_list(manager);
    } else if (strcmp(cmd, "follow") == 0 && n == 2) {
        g_follow = atoi(tok[1]);
        log("follow %d", g_follow);
    } else if (strcmp(cmd, "director") == 0 && n == 2) {
        g_director_period = atof(tok[1]);
        log("director every %.1f s", g_director_period);
    } else if (strcmp(cmd, "sound") == 0) {
        intptr_t volume = n > 1 ? atoi(tok[1]) : -1;
        CloseHandle(CreateThread(nullptr, 0, sound_thread, reinterpret_cast<void*>(volume), 0, nullptr));
    } else if (strcmp(cmd, "unload") == 0) {
        set_slot(reinterpret_cast<void*>(g_original));
        if (g_float_handler) RemoveVectoredExceptionHandler(g_float_handler);
        g_unloading = true;
        log("unloading");
        CloseHandle(CreateThread(nullptr, 0, unload_thread, nullptr, 0, nullptr));
    } else {
        log("unknown command: %s", cmd);
    }
}

void follow(void* manager) {
    if (!g_follow) return;
    uint8_t* proxy = nullptr;
    each_component(manager, kVtableLocalProxy, [&](uint8_t* c) { proxy = c; });
    if (!proxy) return;
    if (g_follow == 1) {
        reinterpret_cast<void (*)(void*, void*, float)>(needle(kProxyUpdate))(proxy, manager, 0.f);
        return;
    }
    if (proxy[kWatchPlayer] != 0xff) return;
    uint8_t* watched = component(manager, proxy + kWatchComponent);
    if (watched) g_set_transform(proxy, g_get_transform(watched));
}

void director(void* manager) {
    if (g_director_period <= 0) return;
    LARGE_INTEGER now;
    QueryPerformanceCounter(&now);
    if (now.QuadPart - g_director_last.QuadPart < g_director_period * g_freq.QuadPart) return;
    g_director_last = now;
    static int team = 0;
    for (int tries = 0; tries < 2; ++tries) {  // the other team, or this one if it has no AI ships
        team ^= 1;
        int action = ai_next(manager, team);
        if (action >= 0) {
            log("director: team %d, AI action %d", team, action);
            return;
        }
    }
}

// Name of the loaded module containing `address` and the offset as a VA at kImageBase, for logs.
void where(const void* address, char* out, size_t size) {
    HMODULE module = nullptr;
    char name[MAX_PATH] = "?";
    if (GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                           static_cast<const char*>(address), &module)) {
        GetModuleFileNameA(module, name, MAX_PATH);
    }
    const char* base = strrchr(name, '\\');
    snprintf(out, size, "%s 0x%llx", base ? base + 1 : name,
             static_cast<unsigned long long>(kImageBase + (reinterpret_cast<uintptr_t>(address) -
                                                           reinterpret_cast<uintptr_t>(module))));
}

// The game runs with floating-point exceptions unmasked, so one NaN (an AI flying a player ship
// went NaN) crashes it. Log where, mask the exceptions on that thread, carry on.
LONG CALLBACK float_handler(EXCEPTION_POINTERS* e) {
    DWORD code = e->ExceptionRecord->ExceptionCode;
    if (code != STATUS_FLOAT_INVALID_OPERATION && code != STATUS_FLOAT_DIVIDE_BY_ZERO &&
        code != STATUS_FLOAT_OVERFLOW && code != STATUS_FLOAT_MULTIPLE_TRAPS &&
        code != STATUS_FLOAT_MULTIPLE_FAULTS)
        return EXCEPTION_CONTINUE_SEARCH;
    static volatile LONG count;
    if (InterlockedIncrement(&count) <= 20) {
        char at[MAX_PATH + 32];
        where(e->ExceptionRecord->ExceptionAddress, at, sizeof at);
        log("float exception %08lx at %s (thread %lu), masked", code, at, GetCurrentThreadId());
    }
    e->ContextRecord->MxCsr |= 0x1f80;  // mask all SSE exceptions
    e->ContextRecord->FltSave.MxCsr |= 0x1f80;
    e->ContextRecord->FltSave.ControlWord |= 0x3f;  // and x87
    return EXCEPTION_CONTINUE_EXECUTION;
}

// Log AI ships whose transform (object at game object +0x1b8, CMatrix34 at +0x90) stops being
// finite, once each, with the last finite position seen.
void watch_nan(void* manager) {
    struct Seen { uint8_t* comp; float x, z; bool bad; };
    static Seen seen[512];
    static int n;
    each_component(manager, kVtableAI, [&](uint8_t* c) {
        auto go = *reinterpret_cast<uint8_t**>(c + 0x10);
        auto obj = go ? *reinterpret_cast<uint8_t**>(go + 0x1b8) : nullptr;
        if (!obj) return;
        auto m = reinterpret_cast<const float*>(obj + 0x90);
        bool finite = true;
        for (int i = 0; i < 12; ++i) finite = finite && m[i] == m[i] && m[i] - m[i] == 0;
        Seen* s = nullptr;
        for (int i = 0; i < n && !s; ++i) if (seen[i].comp == c) s = &seen[i];
        if (!s) {
            if (n == 512) n = 0;  // components are reused across matches; start over
            s = &seen[n++];
            *s = {c, 0, 0, false};
        }
        if (finite) {
            s->x = m[9], s->z = m[11], s->bad = false;
        } else if (!s->bad) {
            s->bad = true;
            log("NaN: AI %p (game object %p) action %d, last at (%.0f, %.0f)", c, go,
                *reinterpret_cast<int*>(c + kAIAction), s->x, s->z);
        }
    });
}

// --- commander: goals for the ai_teams squads ---

// True the first time it's called with a new match's manager (one caller per match check).
bool s_match_changed(void* manager) {
    static void* last;
    if (last == manager) return false;
    last = manager;
    return true;
}

void commander(void* manager) {
    if (g_commander_period <= 0) return;
    LARGE_INTEGER now;
    QueryPerformanceCounter(&now);
    if (now.QuadPart - g_commander_last.QuadPart < g_commander_period * g_freq.QuadPart) return;
    g_commander_last = now;
    static const uint32_t kWaypoint = crc32("NeedleWaypoint"), kCapture = crc32("CapturePoint");
    struct Point { uint8_t* c; float x, y, z; int team; } points[64];
    int np = 0;
    each_of_type(manager, kWaypoint, kCapture, [&](uint8_t* c) {
        if (np == 64) return;
        const float* p = position(c);
        points[np++] = {c, p[0], p[1], p[2], team_at(c, 0x98)};
    });
    static struct { uint8_t* c; int team; } owners[64];
    static int no;
    if (s_match_changed(manager)) no = 0;
    for (int i = 0; i < np; ++i) {
        int k = 0;
        while (k < no && owners[k].c != points[i].c) ++k;
        if (k == no && no < 64) owners[no++] = {points[i].c, points[i].team};
        else if (k < no && owners[k].team != points[i].team) {
            log("capture point (%.0f, %.0f): team %d -> %d", points[i].x, points[i].z, owners[k].team, points[i].team);
            owners[k].team = points[i].team;
        }
    }
    // Our routes: the ai_teams rings (tools/mod_ai_teams.py) are WP 01 in front of one base, WP 02 on
    // the objective, WP 03 in front of the other base, WP 04 on the objective again. No other ring
    // visits one place twice. Found once per match, before they're moved.
    struct Route { uint8_t* ring[4]; bool taken; };
    static Route routes[4];
    static int nr;
    // Our squads: those whose patrol point is on one of our routes when first seen
    struct Squad { uint8_t* squad; uint8_t* ring[4]; uint8_t* target; };
    static Squad squads[4];
    static int ns;
    static void* match;  // components are reallocated for each match: start over
    if (match != manager) {
        match = manager, ns = 0, nr = 0;
        each_component(manager, kVtablePatrol, [&](uint8_t* p) {
            uint8_t* ring[4];
            if (nr == 4 || ring_of(manager, p, ring, 4) != 4 || !component(manager, ring[3] + kPatrolNext)) return;
            for (int i = 1; i < 4; ++i) if (ring[i] < p) return;  // each ring once
            const float *a = position(ring[0]), *b = position(ring[2]), *c = position(ring[1]), *d = position(ring[3]);
            bool twice = (a[0] == b[0] && a[2] == b[2]) || (c[0] == d[0] && c[2] == d[2]);
            if (!twice) return;
            routes[nr] = {{ring[0], ring[1], ring[2], ring[3]}, false};
            ++nr;
        });
        log("commander: %d ai_teams routes", nr);
    }
    each_component(manager, kVtableSquad, [&](uint8_t* sq) {
        Squad* s = nullptr;
        for (int i = 0; i < ns && !s; ++i) if (squads[i].squad == sq) s = &squads[i];
        uint8_t* current = component(manager, sq + kSquadPatrolPoint);
        // (or, failing that, whose leader is near a route point: a squad that just spawned on WP 01 but
        // whose leader took another ring's nearer point; base guards sit ~700 away)
        uint8_t* first_leader = component(manager, sq + kSquadLeader);
        const float* fl = first_leader && !s ? position(first_leader) : nullptr;
        int fteam = first_leader ? team_at(first_leader, 0x80) : -1;
        for (int r = 0; r < nr && !s; ++r) {
            if (routes[r].taken) continue;
            for (int i = 0; i < 4 && !s; ++i) {
                const float* q = position(routes[r].ring[i]);
                bool close_by = fl && (fteam == 0 || fteam == 1) &&
                            (fl[0] - q[0]) * (fl[0] - q[0]) + (fl[2] - q[2]) * (fl[2] - q[2]) < 300 * 300;
                if (routes[r].ring[i] != current && !close_by) continue;
                routes[r].taken = true;
                s = &squads[ns++];
                *s = {sq, {routes[r].ring[0], routes[r].ring[1], routes[r].ring[2], routes[r].ring[3]}, nullptr};
                log("commander: squad %p is on route %d", sq, r);
            }
        }
        if (!s) return;
        uint8_t* leader = component(manager, sq + kSquadLeader);
        if (!leader) return;  // between spawns
        // the squad's team: most of its members' (a ship spawns as team 2 and is given its team after)
        int votes[2] = {};
        auto first = *reinterpret_cast<uint8_t**>(sq + kSquadMembers), last = *reinterpret_cast<uint8_t**>(sq + kSquadMembers + 8);
        for (uint8_t* id = first; id && id < last; id += 16) {
            uint8_t* m = component(manager, id);
            int t = m ? team_at(m, 0x80) : -1;
            if (t == 0 || t == 1) ++votes[t];
        }
        if (!votes[0] && !votes[1]) return;
        int team = votes[1] > votes[0];
        const float* lp = position(leader);
        // keep the target until our team owns it, then take the nearest point we don't own
        Point* target = nullptr;
        auto wanted = [&](const Point& p) { return g_push[team] ? p.team == 1 - team : p.team != team; };
        for (int i = 0; i < np; ++i)
            if (points[i].c == s->target && wanted(points[i])) target = &points[i];
        if (!target) {
            float best = 1e30f;
            for (int i = 0; i < np; ++i) {
                if (!wanted(points[i])) continue;
                float dx = points[i].x - lp[0], dz = points[i].z - lp[2], d = dx * dx + dz * dz;
                if (d < best) best = d, target = &points[i];
            }
            if (!target) return;
            s->target = target->c;
            log("commander: team %d squad %p at (%.0f, %.0f) -> capture point at (%.0f, %.0f), owner %d", team, sq,
                lp[0], lp[2], target->x, target->z, target->team);
        }
        // fly the patrol ring around the point: every capture point has one (its defenders', radius
        // ~160-175, inside the 200 capture radius). Put the squad on its point nearest the leader
        // unless it's already on a point there.
        auto dist2 = [](const float* a, float x, float z) { return (a[0] - x) * (a[0] - x) + (a[2] - z) * (a[2] - z); };
        if (current && dist2(position(current), target->x, target->z) < 250 * 250) return;
        uint8_t* best = nullptr;
        float best_d = 1e30f;
        each_component(manager, kVtablePatrol, [&](uint8_t* p) {
            const float* q = position(p);
            if (dist2(q, target->x, target->z) >= 250 * 250) return;
            float d = dist2(q, lp[0], lp[2]);
            if (d < best_d) best_d = d, best = p;
        });
        if (!best) {
            log("commander: no patrol ring around (%.0f, %.0f)", target->x, target->z);
            return;
        }
        reinterpret_cast<void (*)(void*, void*)>(needle(kSquadSetPatrolPoint))(sq, best);
        log("commander: squad %p onto the ring at (%.0f, %.0f)", sq, target->x, target->z);
    });
}

void tick(void* manager) {
    watch_nan(manager);
    commander(manager);
    director(manager);
    follow(manager);
    char path[MAX_PATH];
    snprintf(path, sizeof path, "%s\\cmd.txt", g_dir);
    FILE* f = fopen(path, "r");
    if (!f) return;
    char lines[4096] = {};
    fread(lines, 1, sizeof lines - 1, f);
    fclose(f);
    DeleteFileA(path);
    for (char* line = lines; line && *line && !g_unloading;) {  // command() uses strtok itself
        char* next = strpbrk(line, "\r\n");
        if (next) *next++ = 0;
        command(manager, line);
        line = next;
    }
}

void* hook_game_controller(void* manager) {
    DWORD self = GetCurrentThreadId();
    LARGE_INTEGER now;
    QueryPerformanceCounter(&now);
    // A new match runs on a new game thread: if ours has been silent for a second, take the caller.
    if (self != g_game_thread && now.QuadPart - g_game_thread_seen > g_freq.QuadPart) {
        g_game_thread = self;
        log("game thread %lu", self);
    }
    if (self == g_game_thread) g_game_thread_seen = now.QuadPart;
    if (self == g_game_thread && !g_unloading &&
        InterlockedCompareExchange(&g_in_tick, 1, 0) == 0) {
        if ((now.QuadPart - g_last.QuadPart) * 1000 >= g_freq.QuadPart * 15) {  // ~once per frame
            g_last = now;
            tick(manager);
        }
        InterlockedExchange(&g_in_tick, 0);
    }
    return g_original(manager);
}

DWORD WINAPI init(LPVOID) {
    GetModuleFileNameA(nullptr, g_dir, MAX_PATH);  // ...\game\<instance>\Bin64\DeadStar.exe
    for (int i = 0; i < 2; ++i) *strrchr(g_dir, '\\') = 0;
    strcat(g_dir, "\\bhs");
    CreateDirectoryA(g_dir, nullptr);
    g_core = reinterpret_cast<uintptr_t>(GetModuleHandleA("GameCore_Steam_Release.dll"));
    g_needle = reinterpret_cast<uintptr_t>(GetModuleHandleA("GameComponentsNeedle_Steam_Release.dll"));
    if (g_needle) {
        auto dos = reinterpret_cast<IMAGE_DOS_HEADER*>(g_needle);
        g_needle_size = reinterpret_cast<IMAGE_NT_HEADERS64*>(g_needle + dos->e_lfanew)->OptionalHeader.SizeOfImage;
    }
    QueryPerformanceFrequency(&g_freq);
    auto core_module = reinterpret_cast<HMODULE>(g_core);
    g_get_transform = reinterpret_cast<GetTransformFn>(
        GetProcAddress(core_module, "?GetGOTransform@CGameObjectComponentGame@@QEBAAEBVCMatrix34@@XZ"));
    g_set_transform = reinterpret_cast<SetTransformFn>(
        GetProcAddress(core_module, "?SetGOTransform@CGameObjectComponentGame@@QEAAXAEBVCMatrix34@@@Z"));
    g_get_velocity = reinterpret_cast<GetVelocityFn>(
        GetProcAddress(core_module, "?GetGOLinearVelocity@CGameObjectComponentGame@@QEBA?AVCVector3@@XZ"));
    g_iat_slot = g_needle ? find_iat_slot(g_needle, "GameCore_Steam_Release.dll", kGameController) : nullptr;
    if (!g_core || !g_iat_slot || !g_get_transform || !g_set_transform) {
        log("init failed: core %p needle %p slot %p", (void*)g_core, (void*)g_needle, g_iat_slot);
        CloseHandle(CreateThread(nullptr, 0, unload_thread, nullptr, 0, nullptr));
        return 0;
    }
    g_original = reinterpret_cast<GameControllerFn>(*g_iat_slot);
    set_slot(reinterpret_cast<void*>(hook_game_controller));
    g_float_handler = AddVectoredExceptionHandler(1, float_handler);
    log("loaded: GameCore %p GameComponentsNeedle %p", (void*)g_core, (void*)g_needle);
    return 0;
}

}  // namespace

BOOL WINAPI DllMain(HINSTANCE dll, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_self = dll;
        DisableThreadLibraryCalls(dll);
        CloseHandle(CreateThread(nullptr, 0, init, nullptr, 0, nullptr));
    }
    return TRUE;
}
