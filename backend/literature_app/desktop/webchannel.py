"""Install the bundled Qt bridge before any frontend code, for file and HTTP pages."""
from PySide6.QtCore import QFile, QIODevice
from PySide6.QtWebChannel import QWebChannel  # load Qt's bundled webchannel resources
from PySide6.QtWebEngineCore import QWebEngineScript


def webchannel_bootstrap_script():
    resource = QFile(":/qtwebchannel/qwebchannel.js")
    if not resource.open(QIODevice.OpenModeFlag.ReadOnly):
        raise RuntimeError("Qt 缺少 qwebchannel.js 資源，請檢查 PySide6 安裝")
    try:
        source = bytes(resource.readAll()).decode("utf-8")
    finally:
        resource.close()
    if not source.strip() or "QWebChannel" not in source:
        raise RuntimeError("Qt qwebchannel.js 資源無效")
    script = QWebEngineScript()
    script.setName("pub-tool-webchannel-bootstrap")
    script.setWorldId(int(QWebEngineScript.ScriptWorldId.MainWorld))
    script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
    script.setRunsOnSubFrames(False)
    script.setSourceCode(source + "\nwindow.QWebChannel = QWebChannel;\n//# sourceURL=pub-tool-webchannel-bootstrap.js\n")
    return script
