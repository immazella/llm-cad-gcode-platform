# LLM Tabanlı CAD ve G-Code Üretim, Düzenleme ve Kontrol Platformu

**Büyük Dil Modeli Temelli G-Kodu Geliştirme ve Kontrolü Sistemi** için çalışan web prototipi.

Kullanıcı doğal dille parça tarif eder veya STEP dosyası yükler, 3B görünümde yüzeylere tıklayıp seçtiği elemanı sohbetle düzenler, **seçtiği CNC makinesi için** 3 eksen freze G-code'u üretir, bu G-code'u (ya da dışarıdan yüklediği bir programı) o makinenin profiline göre deterministik kurallarla doğrular ve takım yolunu animasyonla izler.

Aynı araçlar bir **MCP sunucusu** olarak da sunulur: Claude Desktop, Claude Code veya Cursor içindeki model parçayı doğrudan oluşturup düzenleyebilir, G-code üretip doğrulayabilir (bkz. [MCP ile kullanım](#mcp-ile-kullanım)).

```
┌──────────── React + React Three Fiber (frontend/) ─────────────┐
│ Sohbet paneli │ 3B görüntüleyici (hover/seçim) │ Model/G-code/   │
│               │ Takım yolu animasyonu          │ Ayarlar paneli  │
└───────────────────────────┬─────────────────────────────────────┘
                            │ REST (/api)          Claude Desktop / Claude Code / Cursor
                            │                                  │ MCP (stdio)
                            │                      mcp_server.py ── REST (/api/tools…)
┌──────────── FastAPI (backend/app/main.py) ───────────────────────┐
│ llm/agent.py      LLM orkestratörü (tool calling döngüsü)        │
│ workspace.py      Araçların tek uygulaması (API + LLM ortak)     │
│ cad/              CadQuery/OCC: model ağacı, STEP, tessellation, │
│                   delik/cep tanıma, defeaturing, kazıma yolları  │
│ cam/generator.py  Üretim planı → takım yolu IR'si (cam/ir.py)    │
│ cam/posts/        IR → G-code: Fanuc / LinuxCNC / GRBL post'ları │
│ cam/profiles.py   Makine profilleri (profiles/*.json) + bağlama  │
│ cam/validator.py  Profile duyarlı, seviyeli G-code doğrulama     │
│ cam/interpreter.py G-code ayrıştırma (doğrulama + görselleştirme)│
└──────────────────────────────────────────────────────────────────┘
```

---

## 1. Kurulum

Gerekenler: **Python 3.10–3.13** (önerilen 3.12) ve **Node.js 18+**. Başlatma betikleri `backend/requirements.txt` değiştiğinde eksik paketleri mevcut `.venv`'ye otomatik kurar.

### macOS / Linux

```bash
./run_mac_linux.sh
```

İlk çalıştırmada `.venv` sanal ortamını kurar (CadQuery ~300 MB indirir, birkaç dakika sürebilir), `npm install` yapar ve iki sunucuyu başlatır. Tarayıcıda **http://localhost:5173** adresini açın.

### Windows

```bat
run_windows.bat
```

### Elle kurulum

```bash
python3.12 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt

cd backend && uvicorn app.main:app --reload --port 8000     # 1. terminal
cd frontend && npm install && npm run dev                    # 2. terminal
```

Tek sunucu ile çalıştırmak için: `cd frontend && npm run build`. Sonrasında yalnızca backend'i başlatıp **http://localhost:8000** adresini açmanız yeterli (FastAPI `frontend/dist` klasörünü sunar).

### LLM sağlayıcısı

`backend/.env.example` dosyasını `backend/.env` olarak kopyalayıp bir sağlayıcı seçin:

| Sağlayıcı | Ayar |
|---|---|
| Anthropic Claude | `ANTHROPIC_API_KEY` |
| OpenAI | `OPENAI_API_KEY` |
| DeepSeek | `DEEPSEEK_API_KEY` (isteğe bağlı `DEEPSEEK_MODEL=deepseek-reasoner`) |
| Gemini, Groq, OpenRouter, yerel Ollama… | `LLM_PROVIDER=openai` + `OPENAI_BASE_URL` + `OPENAI_MODEL` |
| Anahtarsız | Hiçbir şey yazmayın → **çevrimdışı mod** |

**Çevrimdışı mod**, gerçek bir LLM değil; aynı araçları çağıran basit, kural tabanlı bir Türkçe ayrıştırıcıdır (`llm/offline.py`). API anahtarı olmadan da arayüzü, CAD ve CAM tarafını göstermek ve test etmek için vardır. Gerçek doğal dil anlayışı (eksik parametreyi sorma, "köşelere dört delik" gibi çıkarımlar, doğrulama raporunu açıklama) için bir API anahtarı tanımlayın. Sağ üstteki rozet hangi modun aktif olduğunu gösterir.

### GitHub'a paylaşmadan önce

- Gerçek API anahtarlarını yalnızca yerel `backend/.env` dosyasına yazın; `.env.example` içine koymayın. `.env` dosyaları Git dışında tutulur.
- Yedek klasörleri, ZIP arşivleri, `backend/data/` oturumları, bağımlılıklar ve derleme çıktıları paylaşılmaz. Eski arşivleri ayrıca yüklemeyin.
- Anahtar daha önce paylaşıldıysa dosyadan silmek yeterli değildir; sağlayıcının panelinden iptal edip yenisini oluşturun.
- İlk commit öncesi `git add --dry-run .` ile dosya listesini kontrol edin. Gitleaks kuruluysa staged içeriği `gitleaks git --pre-commit --staged --redact` ile tarayın.
- İsteğe bağlı FreyaTTS kullanımı için `freya.py` başındaki ayrı kurulum adımlarını izleyin; `third_party/` depoya dahil edilmez.

### MCP ile kullanım

Web uygulamasının yanında ikinci bir giriş noktası olarak bir **MCP sunucusu** vardır (`backend/app/mcp_server.py`). Claude Desktop, Claude Code veya Cursor gibi bir MCP istemcisindeki model, web sohbetindeki araçların aynısını çağırır: *"60×60×10 plaka yap, merkezine Ø20 delik aç, GRBL için G-code üret ve doğrula"*. Bu modda orkestrasyonu istemcinin modeli yapar; `backend/.env` içinde API anahtarı gerekmez.

**Kurulum**

1. Bağımlılıklar: başlatma betiği `requirements.txt` değiştiğinde eksikleri kendisi kurar (yeni paketler `mcp`, `matplotlib`). Elle: `.venv/bin/pip install -r backend/requirements.txt`
2. Bu bilgisayarın yollarıyla istemci yapılandırmasını üretin:
   ```bash
   cd backend && ../.venv/bin/python -m app.mcp_server --print-config
   ```
3. Çıktıyı istemciye ekleyin:
   - **Claude Desktop:** JSON'daki `busproje-cad` girdisini `claude_desktop_config.json` dosyasının `mcpServers` bölümüne ekleyin (macOS: `~/Library/Application Support/Claude/`), sonra Claude Desktop'ı yeniden başlatın.
   - **Cursor:** aynı JSON, `~/.cursor/mcp.json`.
   - **Claude Code:** çıktıdaki `claude mcp add …` satırını çalıştırın.

**Uzaktan bağlantı (claude.ai, ChatGPT…): geçici Cloudflare linki**

Web tabanlı istemciler bilgisayarınızdaki stdio sürecine ulaşamaz; onlar için `run_mcp_remote.sh` MCP sunucusunu HTTP olarak başlatır ve [Cloudflare Quick Tunnel](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/) ile geçici bir https linki açar (hesap gerekmez):

```bash
brew install cloudflared      # bir kez
./run_mcp_remote.sh
```

Betik `https://<rastgele>.trycloudflare.com/mcp/<gizli-anahtar>` biçimindeki linki yazar; bunu istemciye uzak MCP sunucusu olarak ekleyin.

- Link yalnızca betik açıkken çalışır. Ctrl+C ile kapatınca geçersiz olur, her açılışta yeni link (ve yeni anahtar) gelir; istemcideki bağlayıcıyı güncellemek gerekir. Sabit anahtar için `MCP_TOKEN`, farklı port için `MCP_HTTP_PORT`.
- **Güvenlik:** linki bilen herkes araçları kullanabilir; paylaşmayın. Yoldaki anahtarı bilmeyen 404 alır. Bilgisayardaki dosyalara erişen araçlar (`load_step`, `save_gcode`, `add_engraving_svg`, dosya yolları) bu modda listelenmez ve çağrılsa da reddedilir. G-code metin olarak yüklenir; STEP dosyası web arayüzünden yüklenir.
- Quick Tunnel SSE desteklemediği için sunucu düz JSON yanıt veren, oturumsuz Streamable HTTP modunda çalışır. Tünelin çalışma süresi garantisi yoktur; ders demosu için uygundur, kalıcı link için Cloudflare hesabı ve "named tunnel" gerekir.
- Web uygulaması da açıksa (`./run_mac_linux.sh`, ayrı terminal) uzaktaki modelin yaptığı değişiklikler tarayıcıdaki 3B görünümde izlenebilir.

**İki çalışma şekli** (varsayılan `MCP_MODE=auto`, her çağrıda kendiliğinden seçilir):

| Durum | Ne olur |
|---|---|
| Web uygulaması açık (`run_mac_linux.sh`) | MCP sunucusu backend'e bağlanır. Modelin yaptığı her değişiklik tarayıcıdaki 3B görünümde ~1,5 sn içinde görünür. Görüntüleyicide tıkladığınız yüzeyi model `get_selection` ile okur, böylece *"seçtiğim deliği 12 mm yap"* MCP'de de çalışır. |
| Web uygulaması kapalı | Çekirdek MCP sürecinin içinde çalışır; oturum `backend/data/session.pkl`'ye yazılır. Web uygulamasını sonradan açınca model kaldığı yerden görünür. Kullanıcı modeli sohbetteki 3B görüntüleyicide (`show_model_3d`) görür ve seçer. |

Sabitlemek için `MCP_MODE=http` ya da `MCP_MODE=local`; backend başka bir adresteyse `CAD_API_URL`.

**Araçlar:** web sohbetindeki 22 aracın tamamı (şemalar `llm/tool_schemas.py`'den, tek kaynak) ve MCP'ye özgü olanlar:

| Araç | Görevi |
|---|---|
| `show_model_3d` | Sohbetin içinde **etkileşimli 3B görüntüleyici** açar (MCP Apps): döndürme, yakınlaştırma, yüzeye tıklayıp seçme |
| `set_selection` | Yüzey seçimi (görüntüleyici tıklamada çağırır; web arayüzündeki seçimle aynı yere yazılır) |
| `get_context` | Web sohbetindeki [BAĞLAM] bloğunun aynısı: model özeti, seçili yüzey, makine profili, G-code durumu |
| `get_selection` | Web arayüzünde veya `show_model_3d` görüntüleyicisinde seçili yüzey (delik/cep kimliğiyle) |
| `render_model` | Modelin PNG görüntüsü (iso/top/front/right); **modelin kendi kontrolü için** (ChatGPT bu görüntüyü kullanıcıya göstermez) |
| `render_toolpath` | Takım yolu PNG'si (üstten + izometrik; kesme mavi, G0 kesikli kırmızı) |
| `get_gcode` | Program metni, satır numaralı ve sayfalı (doğrulama raporundaki satırlarla aynı) |
| `load_step`, `upload_gcode`, `add_engraving_svg` | Bilgisayardaki STEP / G-code / SVG dosyasını yükler |
| `save_gcode`, `export_step` (`path`) | Programı / modeli bilgisayara kaydeder (varsayılan olarak var olan dosyanın üzerine yazmaz) |
| `list_validation_rules` | Doğrulama kural listesi |

**Sohbet içi 3B görüntüleyici (MCP Apps)**

MCP Apps'i destekleyen istemcilerde (ChatGPT, Claude, VS Code…) `show_model_3d` sonucu sohbette döndürülebilir bir 3B görünüm olarak açılır. Yüzeye tıklamak `set_selection` aracını çağırır ve seçimi modele bildirir; ardından *"seçtiğim deliği 12 mm yap"* doğrudan çalışır.

- Araç tanımındaki `_meta.ui.resourceUri` → `ui://busproje/viewer.html` kaynağını gösterir; istemci bu HTML'i (`text/html;profile=mcp-app`) sandbox'lı bir iframe'de açar.
- Mesh, sonucun `_meta` alanında gider: yalnızca görüntüleyiciye ulaşır, modelin bağlamını şişirmez. Modele kısa bir metin özeti gider.
- Görüntüleyicinin kaynağı `mcp_ui/` (React + Three.js, `@modelcontextprotocol/ext-apps`); `vite-plugin-singlefile` ile tek dosyaya derlenip `backend/app/mcp_ui/index.html`'e yazılır. Derlenmiş dosya depodadır; yalnızca arayüzü değiştirirseniz yeniden derleyin:
  ```bash
  cd mcp_ui && npm install && npm run build
  ```
- Sunucuya araç/kaynak eklendikten sonra istemcideki bağlayıcıyı **yenileyin** (ChatGPT araç listesini önbelleğe alır).
- Yavaşlık teşhisi: `MCP_LOG_LEVEL=INFO` ile her aracın süresi stderr'e yazılır (`araç show_model_3d 13 ms`).

Sunucu, web sohbetindeki sistem talimatını (koordinat kuralları, çap/yarıçap ayrımı, "G-code'u kendin yazma", "geçti/kaldı kararını doğrulayıcı verir") MCP `instructions` alanıyla istemciye iletir. Bu talimatı göstermeyen istemciler için aynı metin `cad_asistani` prompt'u olarak da vardır.

---

## 2. Kullanım senaryoları

| Senaryo | Nasıl |
|---|---|
| **A. Text-to-CAD** | Sohbete: *"60 × 60 × 10 mm kare plaka oluştur. Merkezine 10 mm yarıçaplı bir delik aç."* |
| **B. STEP yükleme** | Üst çubuk → *STEP yükle* (örnek: `examples/ornek_flans.step`). Delikler ve cepler otomatik tanınır. |
| **C. Doğal dille düzenleme** | *"Parçanın merkezindeki deliğin çapını 20 mm'den 30 mm'ye çıkar."* |
| **Kazıma (gravür)** | Model sekmesi → *Kazıma* kartı: yazı (Türkçe karakterler dahil) veya *SVG logo* yükleme; konum, açı, derinlik. Sohbette: *"\"İTÜ\" yazısını 8 mm yüksekliğinde y=10 noktasına kazı"*, *"kenardan 3 mm içeride çerçeve kazı"*, *"x=-20 y=0 x=0 y=10 x=20 y=0 noktalarından çizgi kazı"*. Cep tabanı gibi yukarı bakan bir yüzey seçilerek oraya da kazınabilir. |
| **Seçerek düzenleme** | Deliğin iç yüzeyine fareyle gelin (vurgulanır), tıklayın, sonra: *"Seçtiğim deliğin yarıçapını 15 mm yap."* |
| **Makine seçimi** | G-code sekmesi → *Makine profili* listesi, ya da sohbette *"GRBL makinesine geç"*, *"makine profillerini listele"*. Profil değişince mevcut program yeni makineye göre yeniden doğrulanır. |
| **D. G-code üretimi** | *"G-code üret"*, *"Fanuc için G-code üret"* veya sağ panel → G-code → *Üret*. Program seçili makinenin post-processor'ıyla yazılır; üretim planı, uyarılar ve doğrulama raporu gösterilir. |
| **G-code kontrolü** | G-code sekmesi → *G-code yükle…* (örnek: `examples/hatali_ornek.nc`). Hatalı satırlar kodda renklenir; rapordaki maddeye tıklayınca ilgili satıra gidilir. |
| **E. Takım yolu animasyonu** | Orta panel → *Takım yolu*. Başlat / duraklat / devam / başa sar, hız seçimi ve zaman çubuğu. Animasyon sırasında çalışan G-code satırı sağda vurgulanır. |

Diğer örnekler: *"x=20 y=-15 noktasına 6 mm çaplı 5 mm derinliğinde delik aç"*, *"20x15x4 cep aç x=12 y=15 köşe yarıçapı 3"*, *"dikey kenarlara 4 mm kavis ver"*, *"üst kenarlara 1 mm pah kır"*, *"kalınlığı 15 mm yap"*, *"geri al"*, *"STEP olarak dışa aktar"*.

### Koordinat kuralı

- Birim **mm**. Parça XY düzleminde merkezlidir (X0 Y0), alt yüzey Z=0, üst yüzey Z=yükseklik. Yüklenen STEP dosyaları da bu kurala göre konumlandırılır.
- G-code iş sıfırı: **parça üst yüzeyinin merkezi** (X0 Y0 Z0). Kesmeler negatif Z'dedir.
- Eksen kursları **makine koordinatındadır**. İş sıfırının (G54) tablada nerede olduğu *İşleme ayarları → Bağlama* bölümünden ayarlanır; kurs kontrolü `makine = iş + G54 (+ G43 aktifse takım boyu)` ile yapılır.
- Takım boyu her takımın kendi kaydındadır (*İşleme ayarları → Takımlar* tablosu veya sohbette *"T1 takım boyu 65 mm"*). Takım numaraları sabittir; listeye takım eklemek/silmek diğer takımların numarasını ve boyunu değiştirmez.

---

## 3. Mimari ve tasarım kararları

### LLM ve tool calling
LLM yalnızca orkestratördür; geometri hesabı yapmaz. Her kullanıcı mesajına kompakt bir **[BAĞLAM]** bloğu eklenir: model özeti (ölçüler, özellik ağacı, tanınan delik ve cepler), varsa seçili yüzeyin bilgisi, mevcut G-code durumu. Tam geometri veya mesh LLM'ye gönderilmez.

Araçlar (`llm/tool_schemas.py`): `create_box`, `create_cylinder`, `add_hole`, `add_pocket`, `add_engraving_text`, `add_engraving_path`, `add_fillet`, `add_chamfer`, `modify_base`, `modify_hole`, `modify_feature`, `delete_feature`, `get_geometry_info`, `get_holes`, `get_face_info`, `undo`, `export_step`, `generate_gcode`, `validate_gcode`, `list_machine_profiles`, `set_machine_profile`, `set_tool_length`.

[BAĞLAM] bloğunda seçili makine profili ve G54 konumu da yer alır; LLM doğrulama bulgularını koda, seviyeye ve `data` alanındaki sayılara dayanarak açıklar.

Arayüzdeki butonlar ve sohbetteki komutlar **aynı** fonksiyonları (`workspace.py`) çağırır.

### CAD motoru ve kimlik eşleştirme
- **Parametrik model ağacı:** taban (kutu/silindir) + sıralı özellikler (delik, cep, kavis, pah). Her düzenleme modeli baştan oluşturur. Düzenleme atomiktir: hata olursa model eski hâline döner. 30 adımlık geri alma vardır.
- **Kararlı kimlikler:** Özellikler yüzey numarasına değil, geometrik konuma (origin + normal) bağlı saklanır. Yeniden oluşturmadan sonra her yüzeyin hangi özelliğe ait olduğu, yüzey üzerindeki bir noktanın o özelliğin "takım katısı" sınırına uzaklığıyla bulunur. Bu yüzden kullanıcı seçtiği deliği her zaman `hole_1` olarak görür, OCC yüzey sırası değişse bile.
- **Yüzey bazlı tessellation:** Her B-rep yüzeyi ayrı üçgenlenir ve `f<n>` kimliğiyle gönderilir. Frontend her yüzeyi ayrı mesh olarak çizer. Raycasting ile üzerine gelinen yüzey kimliği doğrudan bilinir. Seçim, model sürüm numarasıyla birlikte gönderilir; eski sürüme ait seçim reddedilir.
- **Özellik tanıma** (`cad/recognition.py`, parametrik ve STEP modellerde aynı): içbükey ve toplamda 360° kapsayan silindir yüzey grupları delik sayılır (köşe radyüsleri ve kavisler elenir). Açık uç / kör uç katı içi testiyle belirlenir. Cep tabanı, üst seviyenin altında +Z normalli ve dış sınıra değmeyen düzlemsel yüzeydir. Kör delik tabanları cep sayılmaz.
- **Kazıma (`cad/engrave.py`):** Kazıma bir çizgi takip özelliğidir; takım merkezi yolu sabit derinlikte izler. Kaynaklar: *yazı / tek çizgi* (Hershey "futural" fontu, harflerin orta çizgisi; Türkçe ç ş ğ ö ü ı İ temel harf + çengel/kısa yay/nokta ile oluşturulur), *yazı / kontur* (sistemdeki TrueType fontun harf dış hatları), *yol* (nokta listesi, açık ya da kapalı) ve *SVG* (svgelements; dönüşümler uygulanır, yay/Bezier eğrileri noktalara bölünür). Vuruşlar özelliğin merkezine göre yerel saklanır, böylece `modify_feature` ile taşıma/döndürme/büyütme yapılabilir. CAD'deki oluk, vuruşların çizgi genişliği kadar kalınlaştırılıp (shapely) derinlik kadar çıkarılmasıdır (düz tabanlı yaklaşım; V-uçla gerçek oluk V kesitlidir). Kazıma yüzeyden taşarsa ya da bir deliğe/boşluğa denk gelirse hata verilir. Oluk yüzeyleri delik/cep olarak tanınmaz. Çizgi genişliği verilmezse takım listesindeki V-uçla o derinlikte oluşacak genişlik kullanılır.
- **STEP delik düzenleme:** Tanınan (parametrik olmayan) bir delik, OCC `BRepAlgoAPI_Defeaturing` ile geometriden kaldırılır ve yeni ölçüyle parametrik delik olarak yeniden açılır. Bu işlemden sonra model "pişirilmiş" gövde üzerinde çalışır.

### Makine profilleri (`cam/profiles.py`, `backend/profiles/*.json`)
Makineye ait her bilgi koddan çıkarılıp JSON profillere taşındı: kontrol ünitesi ve post-processor, desteklenen G/M kodları, eksen kursları (makine koordinatında), ilerleme/hızlı/devir sınırları, takım değiştirici, delme döngüsü desteği, G43 beklentisi, program numarası, varsayılan G54 konumu. Yeni makine eklemek için yeni bir JSON dosyası yeterlidir.

| Profil | Kontrol ünitesi | Öne çıkan farklar |
|---|---|---|
| `fanuc_vmc` | Fanuc 0i tarzı | `%` + `O` numarası, M6 (20 takım), `G43 H`, G81/G83, S ≤ 10000 |
| `linuxcnc_mill` | LinuxCNC | O numarası yok, `G64 P`, M6 elle, S ≤ 6000 (üretici devri ve ilerlemeyi orantılı düşürür) |
| `grbl_router` | GRBL 1.1 | G81/G83 ve M6 yok, `G43 H` yok, Z kursu 80 mm, F ≤ 2000 |

Hepsi **örnek** profillerdir; gerçek bir tezgâhın değerleri kendi parametrelerinden alınmalıdır.

### CAM (G-code üretimi)
`CAD modeli → üretim planı → takım yolu IR'si → post-processor → G-code`, tamamen deterministik. Üretici (`generator.py`) G-code yazmaz; makineden bağımsız bir hareket listesi (`ir.py`: Rapid, Linear, Arc, DrillCycle, ToolChange, Spindle, Coolant) üretir. Seçili profilin post-processor'ı (`posts/`) bunu diyalekte çevirir:

- **Fanuc / LinuxCNC:** `T M6` + ilk Z hareketinde `G43 H`, delme döngüleri `G98 G81/G83 … G80`.
- **GRBL:** delme döngüleri G0/G1 hareketlerine açılır (gagalama dahil), takım değişimi `M5` + `M0 (TAKIM DEGISTIR …)` ile elle yapılır, `%` yazılmaz.

Devir ve ilerleme profilin sınırlarını aşarsa üretici bunları düşürür (devir düşünce ilerleme aynı oranda düşer) ve uyarı verir.

| Operasyon | Yöntem |
|---|---|
| Delme | Takım listesinde çapa uyan matkap varsa `G81`, derinlik > 3D ise `G83` gagalamalı. Boydan boya deliklerde uç payı (0,3D) + taşma eklenir. |
| Helisel delik | Uygun matkap yoksa ve delik frezeden büyükse freze ile `G3` helisel interpolasyon. |
| Cep boşaltma | Cep sınırının içe ofsetleri (yan paso = oran × D), içten dışa, kademeli derinlik, tırmanma yönü (CCW). |
| Kazıma | V-uç (yoksa en küçük parmak freze) her vuruşu sabit derinlikte izler. Vuruşlar en yakın komşu sırasıyla dizilir (açık vuruşlar ters çevrilebilir, kapalılar en yakın köşeden başlar); kademeli derinlikte açık vuruşlar zikzak gider. Vuruşlar arası geçiş stok üst yüzeyinin 1 mm üstünde. Üst yüzey kazıması matkaplardan hemen sonra (ek takım değişimi olmaz), cep tabanı kazıması cepten sonra yapılır. Takım ile istenen çizgi genişliği uyuşmazsa uyarı verilir. |
| Dış kontur | Alt yüzey sınırının takım yarıçapı kadar dışa ofseti, saat yönü (tırmanma), kademeli derinlik, alt yüzeyin 0,5 mm altına kadar. |

Plan takım değişimini azaltacak şekilde sıralanır. Cep tabanındaki delikler cepten önce delindiği için R düzlemi ve helis başlangıcı stok üst yüzeyinin üstünde tutulur. Takım listesi, kesme parametreleri ve bağlama (G54) **İşleme ayarları** sekmesinden değiştirilebilir.

### G-code doğrulama (`cam/validator.py`)

Doğrulama **seçili makine profiline göre** yapılır; aynı program bir makinede geçip diğerinde kalabilir. Her bulgu bir kural kaydına (`RULES`) bağlıdır ve `code`, `level`, `severity`, `rule`, `line`, `message`, `suggestion` ile yapılandırılmış `data` alanı taşır (ör. E040 için eksen, iş ve makine koordinatı, kurs, G54, takım boyu, etkilenen satırlar). Kural listesi: `GET /api/rules`.

| Seviye | Kod | Kontrol |
|---|---|---|
| 1 Sözdizimi ve komut | E001 | Sözdizimi (geçersiz karakter, kapanmayan yorum, iç içe parantez) |
| | E002 | Kontrol ünitesinin desteklemediği G/M kodu (profile göre, diyalekte özgü öneriyle) |
| | E003 / E004 / W005 | Aynı modal gruptan iki kod, tekrarlanan adres harfi, satırda birden çok M |
| | E010, E013, E014 | F tanımsız/sıfır, S olmadan M3, T'siz M6 |
| | E020, E021–E023 | Hareket kodu olmadan eksen; delme döngüsünde eksik Z/R/Q, R < Z |
| | E050–E053, W052 | Yay merkezi eksik, R çok küçük, I/J tutarsızlığı, G18/G19 yayı |
| | W040, W060–W062, I063 | M30 sonrası kod, G21/G90 eksik, program sonu eksik, hareket yok |
| | I070 | Makinede geçerli ama simülasyonda modellenmeyen kod (G28, G53, G92…) |
| 2 Makine uyumluluğu | E040 | Eksen kursu aşımı, **makine koordinatında** (eksen/yön başına tek bulgu) |
| | W011, E012 | İlerleme sınırı, devir alt/üst sınırı |
| | E015, E016, E018 | Takım değiştirici yok (M6), magazin kapasitesi, delme döngüsü yok |
| | W017, W019 | Takım değişiminden sonra G43 yok (Fanuc/LinuxCNC), O numarası yok (Fanuc) |
| | I071 | G43 H ile kullanılan takımın boyu girilmemiş: Z kursu 0 boyla kontrol edildi (üst sınır aşımı gözden kaçabilir) |
| 3 Hareket güvenliği (basit) | E030 | İş mili kapalıyken kesme veya delme |
| | E031 / E032 | Malzeme içinde yatay G0; G0 ile ya da delme döngüsünün R düzlemine inerken malzemeye dalma |
| | W032, W033, I041, W064 | Düşük Z'de / iş mili açıkken takım değişimi, sonda iş mili açık, tablaya kesme riski |

Rapor ayrıca seviye bazında özet, kullanılan profil ve G54, süre tahmini (hareket + takım değişimi) ve **kontrol edilmeyenler** listesini (tutucu/fikstür çarpışması, talaş kaldırma, CAD karşılaştırması, ivme/look-ahead, makrolar, G41/G42) döndürür. Arayüz "Geçti" yerine "Hata yok" der: sonuç yalnızca yapılan kontroller kapsamında geçerlidir.

LLM (API anahtarı varsa) bu raporu sade bir dille açıklar. Hata kararını ise her zaman deterministik kurallar verir.

---

## 4. Proje yapısı

```
backend/
  app/
    main.py              FastAPI uç noktaları (+ MCP köprüsü: /api/tools, /api/selection, /api/context)
    mcp_server.py        MCP sunucusu (stdio/HTTP): aynı araçlar + görüntü, dosya, seçim ve 3B görüntüleyici araçları
    mcp_ui/index.html    Sohbet içi 3B görüntüleyici (mcp_ui/'dan derlenmiş tek dosya, ui:// kaynağı)
    workspace.py         Aktif model, G-code kayıtları, araç uygulamaları
    cad/                 occ_utils.py, features.py, recognition.py, model.py, engrave.py
    cam/                 config.py, profiles.py, ir.py, generator.py, interpreter.py, validator.py
    cam/posts/           base.py (IR → G-code, modal yazıcı), dialects.py (Fanuc, LinuxCNC, GRBL)
    llm/                 agent.py, tool_schemas.py, prompts.py, offline.py
  profiles/              fanuc_vmc.json, linuxcnc_mill.json, grbl_router.json
  tests/                 test_core.py, test_mcp_server.py (pytest, 47 test; MCP testleri gerçek stdio bağlantısıyla)
  requirements.txt, .env.example
frontend/
  src/App.tsx, api.ts, types.ts, styles.css
  src/components/        ModelViewer, ToolpathViewer, ChatPanel, Inspector
mcp_ui/                  MCP Apps görüntüleyicisinin kaynağı (src/main.tsx, Viewer.tsx, mesh.ts; npm run build)
examples/                ornek_flans.step, hatali_ornek.nc
run_mac_linux.sh, run_windows.bat   Web uygulaması
run_mcp_remote.sh        MCP sunucusu + Cloudflare geçici linki
```

Testler: `cd backend && ../.venv/bin/python -m pytest -q`

---

## 5. Sınırlamalar ve olası geliştirmeler

- **Prototip:** Üretilen G-code fiziksel tezgâhta kullanılmadan önce makineye özgü post-processor, simülasyon ve güvenlik kontrolünden geçirilmelidir.
- Tek kullanıcılı, bellek içi çalışma alanı. Sunucu yeniden başlarsa model kaybolur. STEP olarak dışa aktararak saklayın.
- CAM yalnızca +Z yönündeki delikleri, kapalı cepleri ve alt yüzey dış konturunu işler. Yan delikler, adalı cepler, 3B yüzeyler ve tutma köprüleri (tab) yoktur. Cep dalışı düz dalmadır (rampa/helis yok).
- Yan yüzeydeki parametrik deliklerin konumu değiştirilemez (silip yeniden eklenir).
- STEP'te yalnızca delikler düzenlenebilir. Diğer özellikler tanınır ama düzenlenmez.
- Doğrulayıcı G18/G19 yaylarını geometrik olarak kontrol etmez. G28/G30/G53/G92 profil destekliyorsa kabul edilir ama simülasyonda modellenmez (I070); sonrasındaki konum bilinmez sayılır.
- Kazıma yalnızca yukarı bakan yatay yüzeylere yapılır (eğik/silindirik yüzey yok). SVG'deki yazı öğeleri desteklenmez (yola çevrilmeli); dolgulu bölgeler alan olarak boşaltılmaz, yalnızca dış hatları kazınır. Kazıma çizgisinin CAD'deki kesiti V değil düz tabanlıdır.
- Seviye 3 kontrolleri stoku her zaman tam dolu bir blok varsayar; tutucu/fikstür çarpışması ve talaş kaldırma simülasyonu yoktur.
- **MCP:** Uzak bağlantı geçici Quick Tunnel linkiyle ve yoldaki gizli anahtarla korunur; OAuth tabanlı gerçek kimlik doğrulama yoktur. Windows için `run_mcp_remote.sh` karşılığı yok (WSL veya elle: `python -m app.mcp_server --http` + `cloudflared tunnel --url http://127.0.0.1:8765`). Sohbet içi görüntüleyicide takım yolu animasyonu henüz yok (sonraki adım: `show_toolpath` widget'ı).
- Sonraki adımlar (bkz. `claude/mimari-degerlendirme-v2.md`): LLM yama önerisi → diff → onay → yeniden doğrulama döngüsü (Senaryo E), Z-map talaş kaldırma simülasyonu, tutucu/mengene kontrolü, ivmeli süre modeli, hatalı G-code değerlendirme seti.
