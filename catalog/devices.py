"""Device help describes available web access, not nonexistent native apps."""
DEVICES = [
    {"id":"android","logo":"brands/android.svg","logo_kind":"symbol","name":"Android","caption":"Телефоны и планшеты","kind":"phone"},
    {"id":"ios","logo":"brands/apple.svg","logo_kind":"symbol","name":"iPhone и iPad","caption":"Safari · на главный экран","kind":"phone"},
    {"id":"station","logo":"brands/yaosx.png","logo_kind":"badge","name":"ТВ Станция","caption":"Большой экран","kind":"tv"},
    {"id":"samsung","logo":"brands/samsung.svg","logo_kind":"wordmark","name":"Samsung","caption":"Smart TV","kind":"tv"},
    {"id":"lg","logo":"brands/lg.svg","logo_kind":"symbol-wide","name":"LG","caption":"Smart TV","kind":"tv"},
    {"id":"android-tv","logo":"brands/android.svg","logo_kind":"symbol","name":"Android TV","caption":"Телевизоры и приставки","kind":"tv"},
    {"id":"xiaomi","logo":"brands/xiaomi.svg","logo_kind":"symbol","name":"Xiaomi","caption":"Телевизоры и приставки","kind":"tv"},
    {"id":"yaos","logo":"brands/yaos.png","logo_kind":"badge","name":"YaOS","caption":"Телевизоры","kind":"tv"},
    {"id":"haier","logo":"brands/haier.svg","logo_kind":"wordmark","name":"Haier","caption":"Smart TV","kind":"tv"},
    {"id":"hisense","logo":"brands/hisense.svg","logo_kind":"wordmark","name":"Hisense","caption":"Smart TV","kind":"tv"},
    {"id":"tcl","logo":"brands/tcl.svg","logo_kind":"wordmark","name":"TCL","caption":"Smart TV","kind":"tv"},
    {"id":"sony","logo":"brands/sony.svg","logo_kind":"wordmark","name":"Sony","caption":"Smart TV","kind":"tv"},
    {"id":"apple-tv","logo":"brands/appletv.svg","logo_kind":"symbol-wide","name":"Apple TV","caption":"Через совместимое устройство","kind":"tv"},
    {"id":"computer","logo":"brands/computer.svg","logo_kind":"symbol","name":"Компьютер","caption":"Windows · macOS · Linux","kind":"desktop"},
]

def device_guide(key):
    device=next((dict(d) for d in DEVICES if d['id']==key),None)
    if not device: return None
    if key=='android':
        device.update(steps=['Откройте CineScope в Chrome на телефоне или планшете.','Нажмите меню ⋮ и выберите «Добавить на главный экран». Название пункта может зависеть от версии браузера.','Подтвердите добавление. Открывайте CineScope с нового ярлыка и войдите в свой профиль.'],note='Это ярлык сайта. Отдельного приложения CineScope в Google Play пока нет.',source='https://support.google.com/chrome/answer/15085120?co=GENIE.Platform%3DAndroid&hl=ru')
    elif key=='ios':
        device.update(steps=['Откройте CineScope в Safari на iPhone или iPad.','Откройте меню «Поделиться» и выберите «На экран Домой». При необходимости прокрутите список действий.','Нажмите «Добавить». Ярлык CineScope появится на главном экране устройства.'],note='Отдельного приложения CineScope в App Store пока нет. Для каталога и трейлеров нужен интернет.',source='https://support.apple.com/ru-ru/guide/iphone/iphea86e5236/ios')
    elif key=='computer':
        device.update(steps=['Откройте CineScope в актуальной версии вашего браузера.','Добавьте сайт в закладки, чтобы быстро возвращаться к любимым фильмам.','Войдите в профиль: списки, оценки и просмотренное будут доступны на других устройствах.'],note='Ничего скачивать не нужно. Для вывода на телевизор можно подключить компьютер кабелем HDMI.')
    elif key=='apple-tv':
        device.update(steps=['Откройте CineScope на iPhone, iPad или Mac.','Если ваше устройство и Apple TV поддерживают дублирование экрана, используйте его системную функцию.','Если передача видео недоступна, смотрите трейлер на исходном устройстве или подключите компьютер напрямую к телевизору через HDMI.'],note='Приложения CineScope для Apple TV пока нет. Передача видео зависит от устройства и ограничений YouTube.')
    else:
        device.update(steps=['Проверьте, есть ли на вашем телевизоре браузер. Его наличие зависит от модели и системы.','Если браузер есть, введите адрес CineScope, указанный ниже, и откройте сайт.','Если браузера нет или трейлер не запускается, откройте CineScope на компьютере и подключите его к телевизору через HDMI.'],note='Нативного приложения CineScope для этой платформы пока нет. Работа сайта и трейлеров в ТВ-браузере зависит от модели; совместимость со всеми телевизорами не гарантируется.')
    return device
