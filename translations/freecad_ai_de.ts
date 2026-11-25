<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE TS>
<TS version="2.1" language="de_DE">

<!-- InitGui.py -->
<context>
    <name>FreeCADAIWorkbench</name>
    <message>
        <source>FreeCAD AI</source>
        <translation>FreeCAD KI</translation>
    </message>
    <message>
        <source>AI-powered assistant for 3D modeling</source>
        <translation>KI-gestützter Assistent für 3D-Modellierung</translation>
    </message>
</context>

<context>
    <name>OpenChatCommand</name>
    <message>
        <source>Open AI Chat</source>
        <translation>KI-Chat öffnen</translation>
    </message>
    <message>
        <source>Open the FreeCAD AI chat panel</source>
        <translation>Das FreeCAD-KI-Chatfenster öffnen</translation>
    </message>
</context>

<context>
    <name>OpenSettingsCommand</name>
    <message>
        <source>AI Settings</source>
        <translation>KI-Einstellungen</translation>
    </message>
    <message>
        <source>Configure FreeCAD AI providers and options</source>
        <translation>FreeCAD-KI-Anbieter und Optionen konfigurieren</translation>
    </message>
</context>

<!-- chat_widget.py -->
<context>
    <name>ChatDockWidget</name>
    <message>
        <source>FreeCAD AI</source>
        <translation>FreeCAD KI</translation>
    </message>
    <message>
        <source>Plan</source>
        <translation>Planen</translation>
    </message>
    <message>
        <source>Act</source>
        <translation>Ausführen</translation>
    </message>
    <message>
        <source>Mode:</source>
        <translation>Modus:</translation>
    </message>
    <message>
        <source>Settings</source>
        <translation>Einstellungen</translation>
    </message>
    <message>
        <source>Describe what you want to create...</source>
        <translation>Beschreiben Sie, was Sie erstellen möchten...</translation>
    </message>
    <message>
        <source>Send</source>
        <translation>Senden</translation>
    </message>
    <message>
        <source>+ New Chat</source>
        <translation>+ Neuer Chat</translation>
    </message>
    <message>
        <source>Load</source>
        <translation>Laden</translation>
    </message>
    <message>
        <source>Load a previous chat session</source>
        <translation>Eine frühere Chatsitzung laden</translation>
    </message>
    <message>
        <source>Save Log</source>
        <translation>Log speichern</translation>
    </message>
    <message>
        <source>Save session log for debugging</source>
        <translation>Sitzungsprotokoll zum Debuggen speichern</translation>
    </message>
    <message>
        <source>tokens: ~0</source>
        <translation>Token: ~0</translation>
    </message>
    <message>
        <source>tokens: ~{:.1f}k</source>
        <translation>Token: ~{:.1f}k</translation>
    </message>
    <message>
        <source>tokens: ~{}</source>
        <translation>Token: ~{}</translation>
    </message>
    <message>
        <source>No saved sessions found.</source>
        <translation>Keine gespeicherten Sitzungen gefunden.</translation>
    </message>
    <message>
        <source>Load Chat Session</source>
        <translation>Chatsitzung laden</translation>
    </message>
    <message>
        <source>Select a session to resume:</source>
        <translation>Sitzung zum Fortsetzen auswählen:</translation>
    </message>
    <message>
        <source>Resumed session from {}</source>
        <translation>Sitzung fortgesetzt von {}</translation>
    </message>
    <message>
        <source>Failed to load session: {}</source>
        <translation>Sitzung konnte nicht geladen werden: {}</translation>
    </message>
    <message>
        <source>Compacting context (~{}k tokens)...</source>
        <translation>Kontext wird komprimiert (~{}k Token)...</translation>
    </message>
    <message>
        <source>Context compacted to ~{}k tokens</source>
        <translation>Kontext auf ~{}k Token komprimiert</translation>
    </message>
    <message>
        <source>Session log saved to: {}</source>
        <translation>Sitzungsprotokoll gespeichert unter: {}</translation>
    </message>
    <message>
        <source>Failed to save log: {}</source>
        <translation>Log konnte nicht gespeichert werden: {}</translation>
    </message>
    <message>
        <source>Error: </source>
        <translation>Fehler: </translation>
    </message>
    <message>
        <source>All operations completed successfully:</source>
        <translation>Alle Operationen erfolgreich abgeschlossen:</translation>
    </message>
    <message>
        <source>Thinking...</source>
        <translation>Denke nach...</translation>
    </message>
    <message>
        <source>Reached maximum tool call iterations</source>
        <translation>Maximale Anzahl an Werkzeugaufrufen erreicht</translation>
    </message>
    <message>
        <source>Max retries ({}) reached. Please review the error and provide guidance.</source>
        <translation>Maximale Versuche ({}) erreicht. Bitte überprüfen Sie den Fehler und geben Sie Hinweise.</translation>
    </message>
    <message>
        <source>The code failed with the following error:

{}

Please fix the code and try again. (Attempt {}/{})</source>
        <translation>Der Code ist mit folgendem Fehler fehlgeschlagen:

{}

Bitte korrigieren Sie den Code und versuchen Sie es erneut. (Versuch {}/{})</translation>
    </message>
    <message>
        <source>Code executed successfully.</source>
        <translation>Code erfolgreich ausgeführt.</translation>
    </message>
    <message>
        <source>Code execution failed:</source>
        <translation>Codeausführung fehlgeschlagen:</translation>
    </message>
    <message>
        <source>Execute</source>
        <translation>Ausführen</translation>
    </message>
    <message>
        <source>Copy</source>
        <translation>Kopieren</translation>
    </message>
    <message>
        <source>MCP: connected to {}</source>
        <translation>MCP: verbunden mit {}</translation>
    </message>
    <message>
        <source>MCP connection error: {}</source>
        <translation>MCP-Verbindungsfehler: {}</translation>
    </message>
    <message>
        <source>⚠ The system prompt (~{prompt} tokens) and tool definitions (~{tools}) add up to ~{total} tokens before any chat, more than this profile's "Compact above" setting ({window}). If that is your model's real context size, requests may fail or be cut off. Use a larger context on the server; if it is already larger, raise "Compact above".</source>
        <translation>⚠ Der System-Prompt (~{prompt} Token) und die Werkzeugdefinitionen (~{tools}) ergeben zusammen schon vor dem Chat ~{total} Token, mehr als die Einstellung „Komprimieren ab“ dieses Profils ({window}). Falls das der tatsächlichen Kontextgröße Ihres Modells entspricht, können Anfragen fehlschlagen oder abgeschnitten werden. Verwenden Sie auf dem Server einen größeren Kontext; ist er bereits größer, erhöhen Sie „Komprimieren ab“.</translation>
    </message>
    <message>
        <source>Tool Reranking in AI Settings sends fewer tools.</source>
        <translation>Werkzeug-Ranking in den KI-Einstellungen sendet weniger Werkzeuge.</translation>
    </message>
    <message>
        <source>No vision support — configure a vision MCP server or enable in Settings</source>
        <translation>Keine Bildunterstützung — Vision-MCP-Server konfigurieren oder in den Einstellungen aktivieren</translation>
    </message>
    <message>
        <source>Capture</source>
        <translation>Aufnahme</translation>
    </message>
    <message>
        <source>Viewport capture: off</source>
        <translation>Ansichtsaufnahme: aus</translation>
    </message>
    <message>
        <source>⚠ Dangerous mode</source>
        <translation>⚠ Gefahrenmodus</translation>
    </message>
    <message>
        <source>Disable code safety checks and allow running macros from any path. Session-only — resets when FreeCAD restarts.</source>
        <translation>Code-Sicherheitsprüfungen abschalten und Makros aus beliebigen Pfaden erlauben. Gilt nur für diese Sitzung — wird beim Neustart von FreeCAD zurückgesetzt.</translation>
    </message>
    <message>
        <source>⚠ DANGEROUS MODE ACTIVE — safety checks disabled</source>
        <translation>⚠ GEFAHRENMODUS AKTIV — Sicherheitsprüfungen abgeschaltet</translation>
    </message>
    <message>
        <source>Reference</source>
        <translation>Referenz</translation>
    </message>
    <message>
        <source>Attach the current selection as a reference (e.g. @Pad.Face3)</source>
        <translation>Die aktuelle Auswahl als Referenz anhängen (z.B. @Pad.Face3)</translation>
    </message>
    <message>
        <source>Attach</source>
        <translation>Anhängen</translation>
    </message>
    <message>
        <source>Attach a file (image, text, or document)</source>
        <translation>Eine Datei anhängen (Bild, Text oder Dokument)</translation>
    </message>
    <message>
        <source>Attach File</source>
        <translation>Datei anhängen</translation>
    </message>
    <message>
        <source>All supported files (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.txt *.md *.csv *.tsv *.json *.xml *.yaml *.yml *.ini *.cfg *.conf *.toml *.log *.py *.js *.ts *.html *.htm *.css *.sql *.sh *.bash *.svg *.c *.cpp *.h *.hpp *.java *.rs *.go *.rb *.lua *.pdf *.docx *.xlsx *.odt *.rtf);;Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp);;Text files (*.txt *.md *.csv *.json *.xml *.yaml *.py *.js *.ts);;All files (*)</source>
        <translation>Alle unterstützten Dateien (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.txt *.md *.csv *.tsv *.json *.xml *.yaml *.yml *.ini *.cfg *.conf *.toml *.log *.py *.js *.ts *.html *.htm *.css *.sql *.sh *.bash *.svg *.c *.cpp *.h *.hpp *.java *.rs *.go *.rb *.lua *.pdf *.docx *.xlsx *.odt *.rtf);;Bilder (*.png *.jpg *.jpeg *.bmp *.gif *.webp);;Textdateien (*.txt *.md *.csv *.json *.xml *.yaml *.py *.js *.ts);;Alle Dateien (*)</translation>
    </message>
    <message>
        <source>Viewport capture: every message</source>
        <translation>Ansichtsaufnahme: bei jeder Nachricht</translation>
    </message>
    <message>
        <source>Viewport capture: after changes</source>
        <translation>Ansichtsaufnahme: nach Änderungen</translation>
    </message>
    <message>
        <source>Enable Dangerous mode?</source>
        <translation>Gefahrenmodus aktivieren?</translation>
    </message>
    <message>
        <source>Dangerous mode disables the safety checks built into FreeCAD AI.</source>
        <translation>Der Gefahrenmodus schaltet die in FreeCAD KI eingebauten Sicherheitsprüfungen ab.</translation>
    </message>
    <message>
        <source>While active:
• AI-run code may call shell commands, delete files, and touch anything your user account can.
• A macro with an infinite loop will FREEZE FreeCAD with no recovery — unsaved work will be lost.
• Generated code runs against your live document without the headless sandbox pre-check.

You are solely responsible for what you run. Continue?</source>
        <translation>Solange er aktiv ist:
• Von der KI ausgeführter Code kann Shell-Befehle aufrufen, Dateien löschen und auf alles zugreifen, was Ihr Benutzerkonto darf.
• Ein Makro mit einer Endlosschleife FRIERT FreeCAD unwiderruflich EIN — nicht gespeicherte Arbeit geht verloren.
• Generierter Code läuft ohne die Vorprüfung in der Headless-Sandbox direkt in Ihrem geöffneten Dokument.

Sie allein sind dafür verantwortlich, was Sie ausführen. Fortfahren?</translation>
    </message>
    <message>
        <source>⏹ Stopped</source>
        <translation>⏹ Gestoppt</translation>
    </message>
    <message>
        <source>Attach a file (text/document — image attach requires vision)</source>
        <translation>Eine Datei anhängen (Text/Dokument — Bilder erfordern Bildunterstützung)</translation>
    </message>
    <message>
        <source>Nothing selected — select an object, face, or edge in the 3D view or model tree, then click Reference.</source>
        <translation>Nichts ausgewählt — wählen Sie ein Objekt, eine Fläche oder eine Kante in der 3D-Ansicht oder im Modellbaum aus und klicken Sie dann auf „Referenz“.</translation>
    </message>
</context>

<!-- settings_dialog.py -->
<context>
    <name>SettingsDialog</name>
    <message>
        <source>FreeCAD AI Settings</source>
        <translation>FreeCAD-KI-Einstellungen</translation>
    </message>
    <message>
        <source>LLM Provider</source>
        <translation>LLM-Anbieter</translation>
    </message>
    <message>
        <source>Provider:</source>
        <translation>Anbieter:</translation>
    </message>
    <message>
        <source>Enter API key (stored in plaintext)</source>
        <translation>API-Schlüssel eingeben (wird im Klartext gespeichert)</translation>
    </message>
    <message>
        <source>API Key:</source>
        <translation>API-Schlüssel:</translation>
    </message>
    <message>
        <source>Base URL:</source>
        <translation>Basis-URL:</translation>
    </message>
    <message>
        <source>Model name</source>
        <translation>Modellname</translation>
    </message>
    <message>
        <source>Model:</source>
        <translation>Modell:</translation>
    </message>
    <message>
        <source>Parameters</source>
        <translation>Parameter</translation>
    </message>
    <message>
        <source>Maximum output tokens per response.
Context window is determined by the model/provider.</source>
        <translation>Maximale Ausgabe-Token pro Antwort.
Das Kontextfenster wird vom Modell/Anbieter bestimmt.</translation>
    </message>
    <message>
        <source>Max Output Tokens:</source>
        <translation>Max. Ausgabe-Token:</translation>
    </message>
    <message>
        <source>Temperature:</source>
        <translation>Temperatur:</translation>
    </message>
    <message>
        <source>Behavior</source>
        <translation>Verhalten</translation>
    </message>
    <message>
        <source>Auto-execute code in Act mode (skip confirmation dialog)</source>
        <translation>Code im Ausführungsmodus automatisch ausführen (Bestätigungsdialog überspringen)</translation>
    </message>
    <message>
        <source>Thinking:</source>
        <translation>Denkmodus:</translation>
    </message>
    <message>
        <source>Off</source>
        <translation>Aus</translation>
    </message>
    <message>
        <source>On</source>
        <translation>Ein</translation>
    </message>
    <message>
        <source>Extended</source>
        <translation>Erweitert</translation>
    </message>
    <message>
        <source>Off: No reasoning (fastest)
On: Standard thinking/reasoning
Extended: Extended thinking with higher budget</source>
        <translation>Aus: Kein Denken (schnellste)
Ein: Standard-Denkmodus
Erweitert: Erweitertes Denken mit höherem Budget</translation>
    </message>
    <message>
        <source>Sent unchanged in Act mode with tools. In Plan mode, and in Act mode without tools, the "Mode" section is replaced to match, and missing reference sections are added.</source>
        <translation>Wird im Ausführungsmodus mit Werkzeugen unverändert gesendet. Im Planungsmodus und im Ausführungsmodus ohne Werkzeuge wird der Abschnitt „Mode“ passend ersetzt, und fehlende Referenzabschnitte werden ergänzt.</translation>
    </message>
    <message>
        <source>MCP Servers</source>
        <translation>MCP-Server</translation>
    </message>
    <message>
        <source>Add...</source>
        <translation>Hinzufügen...</translation>
    </message>
    <message>
        <source>Remove</source>
        <translation>Entfernen</translation>
    </message>
    <message>
        <source>Test Connection</source>
        <translation>Verbindung testen</translation>
    </message>
    <message>
        <source>Testing...</source>
        <translation>Teste...</translation>
    </message>
    <message>
        <source>Connected! Response: </source>
        <translation>Verbunden! Antwort: </translation>
    </message>
    <message>
        <source>Failed: </source>
        <translation>Fehlgeschlagen: </translation>
    </message>
    <message>
        <source>Save</source>
        <translation>Speichern</translation>
    </message>
    <message>
        <source>Cancel</source>
        <translation>Abbrechen</translation>
    </message>
    <message>
        <source>Provider</source>
        <translation>Anbieter</translation>
    </message>
    <message>
        <source>Tools</source>
        <translation>Werkzeuge</translation>
    </message>
    <message>
        <source>MCP</source>
        <translation>MCP</translation>
    </message>
    <message>
        <source>Profile cannot be used as set up</source>
        <translation>Profil ist so nicht verwendbar</translation>
    </message>
    <message>
        <source>The Base URL for %s still contains a placeholder such as {ACCOUNT_ID}. Replace it with the value from your provider account.</source>
        <translation>Die Basis-URL für %s enthält noch einen Platzhalter wie {ACCOUNT_ID}. Ersetzen Sie ihn durch den Wert aus Ihrem Anbieterkonto.</translation>
    </message>
    <message>
        <source>New profile</source>
        <translation>Neues Profil</translation>
    </message>
    <message>
        <source>Context compaction</source>
        <translation>Kontextkomprimierung</translation>
    </message>
    <message>
        <source>Skill evaluation</source>
        <translation>Skill-Bewertung</translation>
    </message>
    <message>
        <source>Tool optimisation</source>
        <translation>Werkzeugoptimierung</translation>
    </message>
    <message>
        <source>Tool reranking</source>
        <translation>Werkzeug-Ranking</translation>
    </message>
    <message>
        <source>Named connection. Utilities below can each use a different one.</source>
        <translation>Benannte Verbindung. Die Hilfsaufgaben unten können jeweils eine andere verwenden.</translation>
    </message>
    <message>
        <source>New</source>
        <translation>Neu</translation>
    </message>
    <message>
        <source>Rename</source>
        <translation>Umbenennen</translation>
    </message>
    <message>
        <source>Delete</source>
        <translation>Löschen</translation>
    </message>
    <message>
        <source>Profile:</source>
        <translation>Profil:</translation>
    </message>
    <message>
        <source>Use this profile for chat</source>
        <translation>Dieses Profil für den Chat verwenden</translation>
    </message>
    <message>
        <source>The ticked profile is the one the main chat runs on.
Utilities below inherit it unless they name their own.
To move it, tick a different profile — there is always
exactly one.</source>
        <translation>Das angekreuzte Profil ist das, mit dem der Haupt-Chat läuft.
Die Hilfsaufgaben unten übernehmen es, sofern sie kein eigenes angeben.
Um zu wechseln, kreuzen Sie ein anderes Profil an — es ist immer
genau eines.</translation>
    </message>
    <message>
        <source>API key, file:/path/to/token, or cmd:command</source>
        <translation>API-Schlüssel, file:/pfad/zum/token oder cmd:befehl</translation>
    </message>
    <message>
        <source>For secure storage, prefix the value with:
  file:/path/to/keyfile  — read key from a file (re-read each call)
  cmd:some command        — run command, use stdout as the key
Example: cmd:secret-tool lookup service freecad-ai username anthropic</source>
        <translation>Zur sicheren Speicherung stellen Sie dem Wert Folgendes voran:
  file:/pfad/zur/datei  — Schlüssel aus einer Datei lesen (bei jedem Aufruf neu)
  cmd:ein befehl         — Befehl ausführen, stdout als Schlüssel verwenden
Beispiel: cmd:secret-tool lookup service freecad-ai username anthropic</translation>
    </message>
    <message>
        <source>Model supports vision</source>
        <translation>Modell unterstützt Bilder</translation>
    </message>
    <message>
        <source>When enabled, images are sent directly to the LLM.
When disabled, images are described via MCP before sending.
Use Test Connection to auto-detect.</source>
        <translation>Wenn aktiviert, werden Bilder direkt an das LLM gesendet.
Wenn deaktiviert, werden Bilder vor dem Senden über MCP beschrieben.
Mit „Verbindung testen“ automatisch erkennen.</translation>
    </message>
    <message>
        <source>Reset</source>
        <translation>Zurücksetzen</translation>
    </message>
    <message>
        <source>Clear manual override, use auto-detected value</source>
        <translation>Manuelle Einstellung löschen und den automatisch erkannten Wert verwenden</translation>
    </message>
    <message>
        <source>Vision:</source>
        <translation>Bilder:</translation>
    </message>
    <message>
        <source>Use global</source>
        <translation>Globalen Wert verwenden</translation>
    </message>
    <message>
        <source>Compact older messages once the conversation is estimated above this many tokens.
"Use global" takes the value from Behavior → Limits.</source>
        <translation>Ältere Nachrichten komprimieren, sobald die Unterhaltung auf mehr als diese Anzahl Token geschätzt wird.
„Globalen Wert verwenden“ übernimmt den Wert aus Verhalten → Grenzwerte.</translation>
    </message>
    <message>
        <source>Compact above:</source>
        <translation>Komprimieren ab:</translation>
    </message>
    <message>
        <source>Sent to the vendor as-is. Anthropic: a level uses adaptive thinking, a number is a token budget.
Use Test Connection to check a value.</source>
        <translation>Wird unverändert an den Anbieter gesendet. Anthropic: Eine Stufe verwendet adaptives Denken, eine Zahl ist ein Token-Budget.
Mit „Verbindung testen“ lässt sich ein Wert prüfen.</translation>
    </message>
    <message>
        <source>Utility models</source>
        <translation>Modelle für Hilfsaufgaben</translation>
    </message>
    <message>
        <source>Fallback when the chat model can't be reached</source>
        <translation>Ersatz, wenn das Chat-Modell nicht erreichbar ist</translation>
    </message>
    <message>
        <source>Tried once, in this order, after the chat profile fails to answer. A paid profile in this list is used without asking.</source>
        <translation>Wird einmal in dieser Reihenfolge versucht, wenn das Chat-Profil nicht antwortet. Ein kostenpflichtiges Profil in dieser Liste wird ohne Rückfrage verwendet.</translation>
    </message>
    <message>
        <source>Add</source>
        <translation>Hinzufügen</translation>
    </message>
    <message>
        <source>Up</source>
        <translation>Nach oben</translation>
    </message>
    <message>
        <source>Down</source>
        <translation>Nach unten</translation>
    </message>
    <message>
        <source>Rename profile</source>
        <translation>Profil umbenennen</translation>
    </message>
    <message>
        <source>Name:</source>
        <translation>Name:</translation>
    </message>
    <message>
        <source>(manual override)</source>
        <translation>(manuell festgelegt)</translation>
    </message>
    <message>
        <source>Which profile this job runs on. Leave inherited to use the active profile.</source>
        <translation>Mit welchem Profil diese Aufgabe läuft. Unverändert lassen, um das aktive Profil zu verwenden.</translation>
    </message>
    <message>
        <source>Delete profile</source>
        <translation>Profil löschen</translation>
    </message>
    <message>
        <source>Model default</source>
        <translation>Modellstandard</translation>
    </message>
    <message>
        <source>(same as active profile)</source>
        <translation>(wie aktives Profil)</translation>
    </message>
    <message>
        <source>Currently: %s</source>
        <translation>Derzeit: %s</translation>
    </message>
    <message>
        <source>(auto-detected)</source>
        <translation>(automatisch erkannt)</translation>
    </message>
    <message>
        <source>(not tested)</source>
        <translation>(nicht getestet)</translation>
    </message>
    <message>
        <source>Delete profile '{}'?</source>
        <translation>Profil „{}“ löschen?</translation>
    </message>
    <message>
        <source>%s (unknown provider)</source>
        <translation>%s (unbekannter Anbieter)</translation>
    </message>
    <message>
        <source>No Base URL is set for: %s.</source>
        <translation>Keine Basis-URL festgelegt für: %s.</translation>
    </message>
    <message>
        <source>Requests made with such a profile fail with a connection error rather than a clear message. Save anyway?</source>
        <translation>Anfragen mit einem solchen Profil schlagen mit einem Verbindungsfehler statt einer klaren Meldung fehl. Trotzdem speichern?</translation>
    </message>
    <message>
        <source>Limits</source>
        <translation>Grenzwerte</translation>
    </message>
    <message>
        <source>Default output cap per response.
A profile can set its own with a max_tokens row
in its Model Parameters table.</source>
        <translation>Standard-Ausgabelimit pro Antwort.
Ein Profil kann mit einer Zeile max_tokens
in seiner Tabelle „Modellparameter“ ein eigenes festlegen.</translation>
    </message>
    <message>
        <source>Older messages are compacted once the conversation
is estimated above this many tokens.
Default for profiles that don't set their own
on the Provider page.</source>
        <translation>Ältere Nachrichten werden komprimiert, sobald die Unterhaltung
auf mehr als diese Anzahl Token geschätzt wird.
Standard für Profile, die auf der Anbieterseite
keinen eigenen Wert festlegen.</translation>
    </message>
    <message>
        <source>endless</source>
        <translation>endlos</translation>
    </message>
    <message>
        <source>Maximum number of tool-call iterations per response.
0 means no limit (endless). Default: 30.</source>
        <translation>Maximale Anzahl an Werkzeugaufruf-Durchläufen pro Antwort.
0 bedeutet kein Limit (endlos). Standard: 30.</translation>
    </message>
    <message>
        <source>Max tool-loop turns (0 = endless):</source>
        <translation>Max. Werkzeug-Durchläufe (0 = endlos):</translation>
    </message>
    <message>
        <source> s</source>
        <translation> s</translation>
    </message>
    <message>
        <source>Time budget for executing one generated code block (sandbox dry-run and live run).
Raise it for heavy operations on large/detailed models (e.g. scaling). Default: 30.</source>
        <translation>Zeitlimit für die Ausführung eines generierten Codeblocks (Sandbox-Probelauf und echter Lauf).
Erhöhen Sie es für aufwendige Operationen an großen/detaillierten Modellen (z.B. Skalieren). Standard: 30.</translation>
    </message>
    <message>
        <source>Code execution timeout:</source>
        <translation>Zeitlimit für Codeausführung:</translation>
    </message>
    <message>
        <source>Use tool calling (uncheck to fall back to code generation)</source>
        <translation>Werkzeugaufrufe verwenden (deaktivieren, um auf Codegenerierung zurückzufallen)</translation>
    </message>
    <message>
        <source>Keep chat panel open when switching workbenches</source>
        <translation>Chatfenster beim Wechsel des Arbeitsbereichs geöffnet lassen</translation>
    </message>
    <message>
        <source>When enabled, the FreeCAD AI chat panel stays docked and usable in other workbenches instead of hiding when you leave the FreeCAD AI workbench.</source>
        <translation>Wenn aktiviert, bleibt das FreeCAD-KI-Chatfenster in anderen Arbeitsbereichen angedockt und nutzbar, statt beim Verlassen des FreeCAD-KI-Arbeitsbereichs ausgeblendet zu werden.</translation>
    </message>
    <message>
        <source>Strip thinking from conversation history</source>
        <translation>Denkinhalte aus dem Gesprächsverlauf entfernen</translation>
    </message>
    <message>
        <source>Remove thinking/reasoning content from previous turns
before sending to the API. Required by some models
(e.g. Gemma) that reject thinking content in history.

Auto-detected by model name. Check/uncheck to override.</source>
        <translation>Denkinhalte früherer Antworten vor dem Senden an die API
entfernen. Für manche Modelle (z.B. Gemma) erforderlich,
die Denkinhalte im Verlauf ablehnen.

Wird anhand des Modellnamens erkannt. An-/Abwählen überschreibt das.</translation>
    </message>
    <message>
        <source>Keep model reasoning in conversation history</source>
        <translation>Denkinhalte des Modells im Gesprächsverlauf behalten</translation>
    </message>
    <message>
        <source>Store the thinking a model produced for a turn and send
it back with that turn on later requests, which is what
the provider already saw.

Moonshot report a measurable drop in reply quality on
turns whose reasoning is missing, so this is on by
default. Untick it to keep the history as it was before
v0.27.0-alpha.

Models that reject reasoning in history (e.g. Gemma) are
unaffected -- "Strip thinking from conversation
history" above still applies.</source>
        <translation>Die Denkinhalte, die ein Modell für eine Antwort erzeugt hat,
speichern und bei späteren Anfragen mit dieser Antwort
zurücksenden, so wie der Anbieter sie bereits gesehen hat.

Moonshot berichtet von messbar schlechteren Antworten,
wenn diese Denkinhalte fehlen, daher ist dies
standardmäßig aktiviert. Deaktivieren Sie es, um den
Verlauf wie vor v0.27.0-alpha zu halten.

Modelle, die Denkinhalte im Verlauf ablehnen (z.B. Gemma),
sind nicht betroffen -- „Denkinhalte aus dem Gesprächsverlauf
entfernen“ oben gilt weiterhin.</translation>
    </message>
    <message>
        <source>Optimize prompt for caching (may change replies)</source>
        <translation>Prompt für Caching optimieren (kann Antworten verändern)</translation>
    </message>
    <message>
        <source>Providers discount a prompt they have seen before, but
only while its opening stays byte-identical. The live
document state sits at the top of the prompt, so it
changes every time you add a feature and the discount
is lost -- including on the much larger tool list
behind it.

This moves the document state to the end of your last
message instead, and marks a cache point on Anthropic.

The model still sees the same information, but in a
different place, so its replies may differ. That is why
this is off by default.</source>
        <translation>Anbieter gewähren Rabatt auf einen bereits gesehenen Prompt,
aber nur, solange sein Anfang Byte für Byte gleich bleibt.
Der aktuelle Dokumentzustand steht am Anfang des Prompts,
ändert sich also mit jedem neuen Feature, und der Rabatt
geht verloren -- auch für die viel größere Werkzeugliste
dahinter.

Diese Option verschiebt den Dokumentzustand an das Ende
Ihrer letzten Nachricht und setzt bei Anthropic einen
Cache-Punkt.

Das Modell sieht weiterhin dieselben Informationen, aber an
anderer Stelle, daher können seine Antworten abweichen.
Deshalb ist dies standardmäßig deaktiviert.</translation>
    </message>
    <message>
        <source>Log token usage to the Report view</source>
        <translation>Token-Verbrauch in der Ausgabeansicht protokollieren</translation>
    </message>
    <message>
        <source>Print one line per reply with the prompt and completion
token counts, and how much of the prompt was served
from cache.

Turn this on first to see what your requests cost now,
then turn on the caching option above and compare.

On OpenAI-style providers this adds a field to the
request asking for the counts, which a few unusual
endpoints may reject.</source>
        <translation>Pro Antwort eine Zeile mit der Anzahl der Prompt- und
Antwort-Token ausgeben und wie viel des Prompts aus dem
Cache kam.

Aktivieren Sie zuerst dies, um zu sehen, was Ihre Anfragen
derzeit kosten, dann die Caching-Option oben, und
vergleichen Sie.

Bei Anbietern im OpenAI-Stil wird der Anfrage ein Feld
hinzugefügt, das diese Zahlen anfordert; einige
ungewöhnliche Endpunkte lehnen es eventuell ab.</translation>
    </message>
    <message>
        <source>Off: No auto-capture
Every Message: Capture screenshot with each message
After Changes: Capture after tool calls modify the document</source>
        <translation>Aus: Keine automatische Aufnahme
Jede Nachricht: Bildschirmfoto mit jeder Nachricht aufnehmen
Nach Änderungen: Aufnehmen, nachdem Werkzeugaufrufe das Dokument geändert haben</translation>
    </message>
    <message>
        <source>Saved Files</source>
        <translation>Gespeicherte Dateien</translation>
    </message>
    <message>
        <source>Older files are deleted the next time one of that kind is saved. A file is kept only if it passes both the count and the age limit.</source>
        <translation>Ältere Dateien werden gelöscht, wenn das nächste Mal eine derselben Art gespeichert wird. Eine Datei bleibt nur erhalten, wenn sie sowohl die Anzahl- als auch die Altersgrenze einhält.</translation>
    </message>
    <message>
        <source>Saved chat conversations (the ones Load lists): keep only
the newest this many.</source>
        <translation>Gespeicherte Chats (die „Laden“ auflistet): nur die
neuesten so vielen behalten.</translation>
    </message>
    <message>
        <source>Conversations to keep:</source>
        <translation>Zu behaltende Chats:</translation>
    </message>
    <message>
        <source>Session logs written by Save Log: keep only the newest
this many.</source>
        <translation>Von „Log speichern“ geschriebene Sitzungsprotokolle:
nur die neuesten so vielen behalten.</translation>
    </message>
    <message>
        <source>Session logs to keep:</source>
        <translation>Zu behaltende Sitzungsprotokolle:</translation>
    </message>
    <message>
        <source>Recovery snapshots taken before code runs on a document
(Restore from Backup): keep only the newest this many.
Each document reuses one snapshot file.</source>
        <translation>Wiederherstellungs-Schnappschüsse, die vor dem Ausführen von
Code an einem Dokument erstellt werden (Aus Sicherung
wiederherstellen): nur die neuesten so vielen behalten.
Jedes Dokument verwendet eine einzige Schnappschussdatei wieder.</translation>
    </message>
    <message>
        <source>Document backups to keep:</source>
        <translation>Zu behaltende Dokumentsicherungen:</translation>
    </message>
    <message>
        <source> days</source>
        <translation> Tage</translation>
    </message>
    <message>
        <source>Delete conversations, session logs and backups older than
this many days, whatever the counts above say.</source>
        <translation>Chats, Sitzungsprotokolle und Sicherungen löschen, die älter
als diese Anzahl Tage sind, unabhängig von den Anzahlen oben.</translation>
    </message>
    <message>
        <source>Delete files older than:</source>
        <translation>Dateien löschen, die älter sind als:</translation>
    </message>
    <message>
        <source>System Prompt</source>
        <translation>System-Prompt</translation>
    </message>
    <message>
        <source>Reset to Default</source>
        <translation>Auf Standard zurücksetzen</translation>
    </message>
    <message>
        <source>Custom system prompt instructions. Dynamic sections (document state, skills, AGENTS.md) are always appended automatically.</source>
        <translation>Eigene System-Prompt-Anweisungen. Dynamische Abschnitte (Dokumentzustand, Skills, AGENTS.md) werden immer automatisch angehängt.</translation>
    </message>
    <message>
        <source>Thinking (default for profiles):</source>
        <translation>Denkmodus (Standard für Profile):</translation>
    </message>
    <message>
        <source>Viewport capture:</source>
        <translation>Ansichtsaufnahme:</translation>
    </message>
    <message>
        <source>Every Message</source>
        <translation>Jede Nachricht</translation>
    </message>
    <message>
        <source>After Changes</source>
        <translation>Nach Änderungen</translation>
    </message>
    <message>
        <source>Capture resolution:</source>
        <translation>Auflösung der Aufnahme:</translation>
    </message>
    <message>
        <source>Low (400x300)</source>
        <translation>Niedrig (400x300)</translation>
    </message>
    <message>
        <source>Medium (800x600)</source>
        <translation>Mittel (800x600)</translation>
    </message>
    <message>
        <source>High (1280x960)</source>
        <translation>Hoch (1280x960)</translation>
    </message>
    <message>
        <source>keep all</source>
        <translation>alle behalten</translation>
    </message>
    <message>
        <source>Edit...</source>
        <translation>Bearbeiten...</translation>
    </message>
    <message>
        <source>Built-in MCP Server</source>
        <translation>Eingebauter MCP-Server</translation>
    </message>
    <message>
        <source>Server host:</source>
        <translation>Server-Host:</translation>
    </message>
    <message>
        <source>Server port:</source>
        <translation>Server-Port:</translation>
    </message>
    <message>
        <source>Allowed Host headers:</source>
        <translation>Erlaubte Host-Header:</translation>
    </message>
    <message>
        <source>none — authentication disabled</source>
        <translation>keines — Authentifizierung deaktiviert</translation>
    </message>
    <message>
        <source>The token itself, file:/path to read it from a file, or cmd:command to run, e.g. a keyring lookup (cmd:secret-tool lookup service freecad-ai-mcp). Read when the server starts.</source>
        <translation>Das Token selbst, file:/pfad, um es aus einer Datei zu lesen, oder cmd:befehl, um einen Befehl auszuführen, z.B. eine Schlüsselbund-Abfrage (cmd:secret-tool lookup service freecad-ai-mcp). Wird beim Start des Servers gelesen.</translation>
    </message>
    <message>
        <source>Generate</source>
        <translation>Erzeugen</translation>
    </message>
    <message>
        <source>Clear</source>
        <translation>Leeren</translation>
    </message>
    <message>
        <source>Bearer token:</source>
        <translation>Bearer-Token:</translation>
    </message>
    <message>
        <source>Start the MCP server when FreeCAD starts</source>
        <translation>MCP-Server beim Start von FreeCAD starten</translation>
    </message>
    <message>
        <source>Without a bearer token, anything that can reach this address can run FreeCAD tools, including arbitrary Python. Keep it on 127.0.0.1 unless you understand the exposure, or set a bearer token above.

Leave Allowed Host headers empty for loopback-only access. Listing hosts is what makes a non-loopback bind reachable, so name only the addresses clients actually dial. "*" is not accepted — without a token, this list is the only thing limiting who can reach the server.

Host, port, allowed hosts, and the bearer token only take effect the next time the MCP server starts. Saving here does not reconfigure one that is already running.</source>
        <translation>Ohne Bearer-Token kann alles, was diese Adresse erreicht, FreeCAD-Werkzeuge ausführen, einschließlich beliebigem Python-Code. Belassen Sie sie auf 127.0.0.1, sofern Sie das Risiko nicht verstehen, oder legen Sie oben ein Bearer-Token fest.

Lassen Sie die erlaubten Host-Header leer, um nur Loopback-Zugriff zu erlauben. Erst das Eintragen von Hosts macht eine Nicht-Loopback-Adresse erreichbar, nennen Sie also nur die Adressen, die Clients tatsächlich aufrufen. „*“ wird nicht akzeptiert — ohne Token begrenzt allein diese Liste, wer den Server erreichen kann.

Host, Port, erlaubte Hosts und das Bearer-Token werden erst beim nächsten Start des MCP-Servers wirksam. Speichern konfiguriert einen bereits laufenden Server nicht um.</translation>
    </message>
    <message>
        <source>Copy</source>
        <translation>Kopieren</translation>
    </message>
    <message>
        <source>Connect an external agent</source>
        <translation>Externen Agenten verbinden</translation>
    </message>
    <message>
        <source>URL:</source>
        <translation>URL:</translation>
    </message>
    <message>
        <source>none — no bearer token set</source>
        <translation>keiner — kein Bearer-Token festgelegt</translation>
    </message>
    <message>
        <source>Header:</source>
        <translation>Header:</translation>
    </message>
    <message>
        <source>JSON config:</source>
        <translation>JSON-Konfiguration:</translation>
    </message>
    <message>
        <source>Reset to defaults</source>
        <translation>Auf Standard zurücksetzen</translation>
    </message>
    <message>
        <source>Agent:</source>
        <translation>Agent:</translation>
    </message>
    <message>
        <source>Command:</source>
        <translation>Befehl:</translation>
    </message>
    <message>
        <source>Values follow the fields above and take effect the next time the MCP server starts. Edit the command list in config.json (mcp_agent_recipes) while FreeCAD is closed: FreeCAD writes its own copy back on the next save.</source>
        <translation>Die Werte folgen den Feldern oben und werden beim nächsten Start des MCP-Servers wirksam. Bearbeiten Sie die Befehlsliste in config.json (mcp_agent_recipes), während FreeCAD geschlossen ist: FreeCAD schreibt beim nächsten Speichern seine eigene Kopie zurück.</translation>
    </message>
    <message>
        <source>Reset agent commands</source>
        <translation>Agentenbefehle zurücksetzen</translation>
    </message>
    <message>
        <source>Replace the agent command list with the defaults?

The current list is first saved to a backup file in the config directory. The change is saved when you press OK in this dialog.</source>
        <translation>Die Liste der Agentenbefehle durch die Standardwerte ersetzen?

Die aktuelle Liste wird zuvor in einer Sicherungsdatei im Konfigurationsverzeichnis gespeichert. Die Änderung wird gespeichert, wenn Sie in diesem Dialog auf OK klicken.</translation>
    </message>
    <message>
        <source>Replace token source?</source>
        <translation>Token-Quelle ersetzen?</translation>
    </message>
    <message>
        <source>The token is read from a file or command. Replace that with a new token stored in config.json?</source>
        <translation>Das Token wird aus einer Datei oder einem Befehl gelesen. Durch ein neues, in config.json gespeichertes Token ersetzen?</translation>
    </message>
    <message>
        <source>Previous list saved to:</source>
        <translation>Vorherige Liste gespeichert unter:</translation>
    </message>
    <message>
        <source>Token not copied</source>
        <translation>Token nicht kopiert</translation>
    </message>
    <message>
        <source>From that tool's docs, not tested by the maintainer.</source>
        <translation>Aus der Dokumentation dieses Werkzeugs, vom Maintainer nicht getestet.</translation>
    </message>
    <message>
        <source>Could not write the backup, nothing was changed:</source>
        <translation>Die Sicherung konnte nicht geschrieben werden, nichts wurde geändert:</translation>
    </message>
    <message>
        <source>Test Reranker</source>
        <translation>Reranker testen</translation>
    </message>
    <message>
        <source>Send a small test prompt to the reranker LLM using
its resolved profile. Reports success or the exact
error from the provider — useful for diagnosing 4xx
errors, timeouts, or unparseable responses.</source>
        <translation>Einen kleinen Test-Prompt mit dem zugehörigen Profil an das
Reranker-LLM senden. Meldet Erfolg oder den genauen Fehler
des Anbieters — hilfreich bei 4xx-Fehlern, Zeitüberschreitungen
oder nicht auswertbaren Antworten.</translation>
    </message>
    <message>
        <source>Model Parameters</source>
        <translation>Modellparameter</translation>
    </message>
    <message>
        <source>Parameters are merged into the API request body.
Common: temperature, top_p, top_k, n, max_tokens,
presence_penalty, frequency_penalty, repetition_penalty.
max_tokens overrides Max Output Tokens for this profile.
Values are auto-detected as number or string.</source>
        <translation>Parameter werden in den Body der API-Anfrage übernommen.
Üblich: temperature, top_p, top_k, n, max_tokens,
presence_penalty, frequency_penalty, repetition_penalty.
max_tokens überschreibt „Max. Ausgabe-Token“ für dieses Profil.
Werte werden automatisch als Zahl oder Text erkannt.</translation>
    </message>
    <message>
        <source>Load Defaults</source>
        <translation>Standardwerte laden</translation>
    </message>
    <message>
        <source>Load recommended parameters for the current provider</source>
        <translation>Empfohlene Parameter für den aktuellen Anbieter laden</translation>
    </message>
    <message>
        <source>Vision: supported</source>
        <translation>Bilder: unterstützt</translation>
    </message>
    <message>
        <source>Vision: not supported</source>
        <translation>Bilder: nicht unterstützt</translation>
    </message>
    <message>
        <source>Parameters sent with each request (saved per profile):</source>
        <translation>Mit jeder Anfrage gesendete Parameter (pro Profil gespeichert):</translation>
    </message>
    <message>
        <source>Parameter</source>
        <translation>Parameter</translation>
    </message>
    <message>
        <source>Value</source>
        <translation>Wert</translation>
    </message>
    <message>
        <source>Testing "{}"...</source>
        <translation>Teste „{}“...</translation>
    </message>
    <message>
        <source>No profile configured</source>
        <translation>Kein Profil konfiguriert</translation>
    </message>
    <message>
        <source>Capabilities: </source>
        <translation>Fähigkeiten: </translation>
    </message>
    <message>
        <source>OK</source>
        <translation>OK</translation>
    </message>
    <message>
        <source>Error</source>
        <translation>Fehler</translation>
    </message>
    <message>
        <source>Delete user copy of '{name}' and revert to the built-in version?</source>
        <translation>Benutzerkopie von '{name}' löschen und zur eingebauten Version zurückkehren?</translation>
    </message>
    <message>
        <source>Tool Reranking</source>
        <translation>Werkzeug-Ranking</translation>
    </message>
    <message>
        <source>Off: send all tool schemas every turn
Keyword: IDF-weighted token match, no extra LLM call
LLM: semantic ranking via a small/fast LLM
Both keyword and LLM include pinned tools unconditionally.</source>
        <translation>Aus: bei jeder Anfrage alle Werkzeugschemata senden
Stichwort: IDF-gewichteter Wortabgleich, kein zusätzlicher LLM-Aufruf
LLM: semantisches Ranking durch ein kleines/schnelles LLM
Stichwort und LLM schließen angeheftete Werkzeuge immer ein.</translation>
    </message>
    <message>
        <source>comma-separated tool names, always included</source>
        <translation>durch Kommas getrennte Werkzeugnamen, immer enthalten</translation>
    </message>
    <message>
        <source>User Tools</source>
        <translation>Benutzerwerkzeuge</translation>
    </message>
    <message>
        <source>New...</source>
        <translation>Neu...</translation>
    </message>
    <message>
        <source>Reload</source>
        <translation>Neu laden</translation>
    </message>
    <message>
        <source>Also scan FreeCAD macro directory</source>
        <translation>Auch das FreeCAD-Makroverzeichnis durchsuchen</translation>
    </message>
    <message>
        <source>Skills</source>
        <translation>Skills</translation>
    </message>
    <message>
        <source>Reset to Built-in</source>
        <translation>Auf eingebaute Version zurücksetzen</translation>
    </message>
    <message>
        <source>Delete the user copy and revert to the built-in version</source>
        <translation>Die Benutzerkopie löschen und zur eingebauten Version zurückkehren</translation>
    </message>
    <message>
        <source>Refresh</source>
        <translation>Aktualisieren</translation>
    </message>
    <message>
        <source>Extra skill folders (one per line), e.g. ~/.claude/skills. Skills there can run Python inside FreeCAD; only add folders you trust.</source>
        <translation>Zusätzliche Skill-Ordner (einer pro Zeile), z.B. ~/.claude/skills. Skills darin können Python in FreeCAD ausführen; fügen Sie nur Ordner hinzu, denen Sie vertrauen.</translation>
    </message>
    <message>
        <source>Browse…</source>
        <translation>Durchsuchen…</translation>
    </message>
    <message>
        <source>Hooks</source>
        <translation>Hooks</translation>
    </message>
    <message>
        <source>Editor</source>
        <translation>Editor</translation>
    </message>
    <message>
        <source>Open hooks and user tools in the OS-default editor (instead of FreeCAD's docked script editor)</source>
        <translation>Hooks und Benutzerwerkzeuge im Standardeditor des Betriebssystems öffnen</translation>
    </message>
    <message>
        <source>When enabled, files open via the OS file association (xdg-open / Launch Services) so this window can stay open. When disabled, files open in FreeCAD's docked Gui::PythonEditor — which requires closing this window first.</source>
        <translation>Wenn aktiviert, werden Dateien über die Dateizuordnung des Betriebssystems geöffnet (xdg-open / Launch Services), sodass dieses Fenster geöffnet bleiben kann. Wenn deaktiviert, werden Dateien in FreeCADs angedocktem Gui::PythonEditor geöffnet — dafür muss dieses Fenster zuerst geschlossen werden.</translation>
    </message>
    <message>
        <source>Select Tool File</source>
        <translation>Werkzeugdatei auswählen</translation>
    </message>
    <message>
        <source>Python Files (*.py *.FCMacro)</source>
        <translation>Python-Dateien (*.py *.FCMacro)</translation>
    </message>
    <message>
        <source>New User Tool</source>
        <translation>Neues Benutzerwerkzeug</translation>
    </message>
    <message>
        <source>Enter a function name (used as filename and function name):</source>
        <translation>Geben Sie einen Funktionsnamen ein (wird als Datei- und Funktionsname verwendet):</translation>
    </message>
    <message>
        <source>Select hook.py file</source>
        <translation>hook.py-Datei auswählen</translation>
    </message>
    <message>
        <source>Python files (*.py)</source>
        <translation>Python-Dateien (*.py)</translation>
    </message>
    <message>
        <source>Hook Name</source>
        <translation>Hook-Name</translation>
    </message>
    <message>
        <source>Enter a name for this hook:</source>
        <translation>Geben Sie einen Namen für diesen Hook ein:</translation>
    </message>
    <message>
        <source>New Hook</source>
        <translation>Neuer Hook</translation>
    </message>
    <message>
        <source>Open Script Editor</source>
        <translation>Skripteditor öffnen</translation>
    </message>
    <message>
        <source>FreeCAD's script editor is docked behind this dialog. The dialog must close so you can reach it.

Save your pending settings changes first?</source>
        <translation>Der Skripteditor von FreeCAD ist hinter diesem Dialog angedockt. Der Dialog muss geschlossen werden, damit Sie ihn erreichen.

Ihre ausstehenden Einstellungsänderungen zuerst speichern?</translation>
    </message>
    <message>
        <source>Choose a skills folder</source>
        <translation>Skill-Ordner auswählen</translation>
    </message>
    <message>
        <source>Reset Skill</source>
        <translation>Skill zurücksetzen</translation>
    </message>
    <message>
        <source>Method:</source>
        <translation>Methode:</translation>
    </message>
    <message>
        <source>Keyword (free, lexical)</source>
        <translation>Stichwort (kostenlos, lexikalisch)</translation>
    </message>
    <message>
        <source>LLM (semantic)</source>
        <translation>LLM (semantisch)</translation>
    </message>
    <message>
        <source>Top N:</source>
        <translation>Top N:</translation>
    </message>
    <message>
        <source>Pinned tools:</source>
        <translation>Angeheftete Werkzeuge:</translation>
    </message>
    <message>
        <source>File Exists</source>
        <translation>Datei existiert bereits</translation>
    </message>
    <message>
        <source>Invalid Name</source>
        <translation>Ungültiger Name</translation>
    </message>
    <message>
        <source>Name must be a valid Python identifier (letters, digits, underscore).</source>
        <translation>Der Name muss ein gültiger Python-Bezeichner sein (Buchstaben, Ziffern, Unterstrich).</translation>
    </message>
    <message>
        <source>Hook Exists</source>
        <translation>Hook existiert bereits</translation>
    </message>
    <message>
        <source>Remove Hook</source>
        <translation>Hook entfernen</translation>
    </message>
    <message>
        <source>already exists in tools directory.</source>
        <translation>existiert bereits im Werkzeugverzeichnis.</translation>
    </message>
    <message>
        <source>' already exists.</source>
        <translation>' existiert bereits.</translation>
    </message>
    <message>
        <source>Cannot Remove</source>
        <translation>Entfernen nicht möglich</translation>
    </message>
    <message>
        <source>Built-in hooks cannot be removed. You can disable them instead.</source>
        <translation>Eingebaute Hooks können nicht entfernt werden. Sie können sie stattdessen deaktivieren.</translation>
    </message>
    <message>
        <source>A hook named '</source>
        <translation>Ein Hook namens '</translation>
    </message>
    <message>
        <source>Remove hook '</source>
        <translation>Hook entfernen: '</translation>
    </message>
    <message>
        <source>Compatibility: </source>
        <translation>Kompatibilität: </translation>
    </message>
</context>

<context>
    <name>AddMCPServerDialog</name>
    <message>
        <source>Add MCP Server</source>
        <translation>MCP-Server hinzufügen</translation>
    </message>
    <message>
        <source>e.g. filesystem</source>
        <translation>z.B. filesystem</translation>
    </message>
    <message>
        <source>Name:</source>
        <translation>Name:</translation>
    </message>
    <message>
        <source>e.g. npx</source>
        <translation>z.B. npx</translation>
    </message>
    <message>
        <source>Command:</source>
        <translation>Befehl:</translation>
    </message>
    <message>
        <source>e.g. -y @modelcontextprotocol/server-filesystem /tmp</source>
        <translation>z.B. -y @modelcontextprotocol/server-filesystem /tmp</translation>
    </message>
    <message>
        <source>Space-separated arguments</source>
        <translation>Durch Leerzeichen getrennte Argumente</translation>
    </message>
    <message>
        <source>Args:</source>
        <translation>Argumente:</translation>
    </message>
    <message>
        <source>Enabled</source>
        <translation>Aktiviert</translation>
    </message>
    <message>
        <source>Add</source>
        <translation>Hinzufügen</translation>
    </message>
    <message>
        <source>Cancel</source>
        <translation>Abbrechen</translation>
    </message>
    <message>
        <source>Command (stdio)</source>
        <translation>Befehl (stdio)</translation>
    </message>
    <message>
        <source>SSE (URL)</source>
        <translation>SSE (URL)</translation>
    </message>
    <message>
        <source>Streamable HTTP (URL)</source>
        <translation>Streamable HTTP (URL)</translation>
    </message>
    <message>
        <source>Transport:</source>
        <translation>Transport:</translation>
    </message>
    <message>
        <source>e.g. https://host/sse</source>
        <translation>z.B. https://host/sse</translation>
    </message>
    <message>
        <source>URL:</source>
        <translation>URL:</translation>
    </message>
    <message>
        <source>Headers:</source>
        <translation>Header:</translation>
    </message>
    <message>
        <source>Add header</source>
        <translation>Header hinzufügen</translation>
    </message>
    <message>
        <source>Remove header</source>
        <translation>Header entfernen</translation>
    </message>
    <message>
        <source>optional CA bundle path (.pem)</source>
        <translation>optionaler Pfad zum CA-Bundle (.pem)</translation>
    </message>
    <message>
        <source>CA bundle:</source>
        <translation>CA-Bundle:</translation>
    </message>
    <message>
        <source>optional client cert path (.pem)</source>
        <translation>optionaler Pfad zum Client-Zertifikat (.pem)</translation>
    </message>
    <message>
        <source>Client cert:</source>
        <translation>Client-Zertifikat:</translation>
    </message>
    <message>
        <source>optional client key path</source>
        <translation>optionaler Pfad zum Client-Schlüssel</translation>
    </message>
    <message>
        <source>Client key:</source>
        <translation>Client-Schlüssel:</translation>
    </message>
    <message>
        <source> s</source>
        <translation> s</translation>
    </message>
    <message>
        <source>Maximum time to wait for a tool call to complete.
Raise for slow tools (vision models, large builds).
Lower for fast tools where you want to fail quickly.</source>
        <translation>Maximale Wartezeit, bis ein Werkzeugaufruf abgeschlossen ist.
Erhöhen Sie sie für langsame Werkzeuge (Bildmodelle, große Builds).
Verringern Sie sie für schnelle Werkzeuge, die früh fehlschlagen sollen.</translation>
    </message>
    <message>
        <source>Tool call timeout:</source>
        <translation>Zeitlimit für Werkzeugaufrufe:</translation>
    </message>
    <message>
        <source>Deferred tool loading</source>
        <translation>Verzögertes Laden von Werkzeugen</translation>
    </message>
    <message>
        <source>Load tool schemas lazily on first use instead of
fetching all schemas eagerly on connect.
Faster startup when the server exposes many tools.</source>
        <translation>Werkzeugschemata erst bei der ersten Verwendung laden, statt
alle beim Verbinden abzurufen.
Schnellerer Start, wenn der Server viele Werkzeuge anbietet.</translation>
    </message>
    <message>
        <source>Save</source>
        <translation>Speichern</translation>
    </message>
    <message>
        <source>URL is required.</source>
        <translation>Eine URL ist erforderlich.</translation>
    </message>
    <message>
        <source>Edit MCP Server</source>
        <translation>MCP-Server bearbeiten</translation>
    </message>
    <message>
        <source>Header</source>
        <translation>Header</translation>
    </message>
    <message>
        <source>Value</source>
        <translation>Wert</translation>
    </message>
</context>

<!-- code_review_dialog.py -->
<context>
    <name>CodeReviewDialog</name>
    <message>
        <source>Review Code</source>
        <translation>Code überprüfen</translation>
    </message>
    <message>
        <source>Review the proposed code before executing:</source>
        <translation>Vorgeschlagenen Code vor der Ausführung überprüfen:</translation>
    </message>
    <message>
        <source>Edit</source>
        <translation>Bearbeiten</translation>
    </message>
    <message>
        <source>Lock</source>
        <translation>Sperren</translation>
    </message>
    <message>
        <source>Execute</source>
        <translation>Ausführen</translation>
    </message>
    <message>
        <source>Cancel</source>
        <translation>Abbrechen</translation>
    </message>
    <message>
        <source>Code executed successfully.</source>
        <translation>Code erfolgreich ausgeführt.</translation>
    </message>
    <message>
        <source>Execution failed:</source>
        <translation>Ausführung fehlgeschlagen:</translation>
    </message>
    <message>
        <source>Close</source>
        <translation>Schließen</translation>
    </message>
    <message>
        <source>Fix with AI</source>
        <translation>Mit KI beheben</translation>
    </message>
    <message>
        <source>Describe what the AI should fix. Edit the message as needed:</source>
        <translation>Beschreiben Sie, was die KI beheben soll. Passen Sie die Nachricht bei Bedarf an:</translation>
    </message>
    <message>
        <source>Send</source>
        <translation>Senden</translation>
    </message>
    <message>
        <source>Check</source>
        <translation>Prüfen</translation>
    </message>
    <message>
        <source>Validate the code in a headless FreeCAD sandbox against a copy of the current document without modifying anything.</source>
        <translation>Den Code in einer Headless-FreeCAD-Sandbox an einer Kopie des aktuellen Dokuments prüfen, ohne etwas zu verändern.</translation>
    </message>
    <message>
        <source>Compose a message (prefilled with the latest error or a blank template) and send it to the AI to refine the code.</source>
        <translation>Eine Nachricht verfassen (mit dem letzten Fehler oder einer leeren Vorlage vorausgefüllt) und an die KI senden, um den Code zu überarbeiten.</translation>
    </message>
    <message>
        <source>Validated — no errors in sandbox.</source>
        <translation>Geprüft — keine Fehler in der Sandbox.</translation>
    </message>
    <message>
        <source>Validation found issues:</source>
        <translation>Die Prüfung hat Probleme gefunden:</translation>
    </message>
    <message>
        <source>[Describe what to change]</source>
        <translation>[Beschreiben Sie, was geändert werden soll]</translation>
    </message>
    <message>
        <source>[Describe the problem here]</source>
        <translation>[Beschreiben Sie hier das Problem]</translation>
    </message>
    <message>
        <source>I'd like you to change something about this code.</source>
        <translation>Bitte ändere etwas an diesem Code.</translation>
    </message>
    <message>
        <source>The code produced the following error. Please fix it:</source>
        <translation>Der Code hat den folgenden Fehler verursacht. Bitte behebe ihn:</translation>
    </message>
    <message>
        <source>The code ran without errors, but the result isn't what I wanted. Please fix it.</source>
        <translation>Der Code lief ohne Fehler, aber das Ergebnis ist nicht das, was ich wollte. Bitte korrigiere ihn.</translation>
    </message>
</context>

<!-- message_view.py -->
<context>
    <name>MessageView</name>
    <message>
        <source>You</source>
        <translation>Du</translation>
    </message>
    <message>
        <source>AI</source>
        <translation>KI</translation>
    </message>
    <message>
        <source>System</source>
        <translation>System</translation>
    </message>
    <message>
        <source>Code executed successfully</source>
        <translation>Code erfolgreich ausgeführt</translation>
    </message>
    <message>
        <source>Execution failed</source>
        <translation>Ausführung fehlgeschlagen</translation>
    </message>
    <message>
        <source>Calling {}...</source>
        <translation>Rufe {} auf...</translation>
    </message>
    <message>
        <source>Thinking</source>
        <translation>Denke nach</translation>
    </message>
    <message>
        <source>Response was cut off at the output limit ({max_tokens} tokens). Raise Max Output Tokens in Settings → Behavior, or this profile's max_tokens row, or ask the model to continue.</source>
        <translation>Die Antwort wurde am Ausgabelimit ({max_tokens} Token) abgeschnitten. Erhöhen Sie „Max. Ausgabe-Token“ unter Einstellungen → Verhalten oder die Zeile max_tokens dieses Profils, oder bitten Sie das Modell fortzufahren.</translation>
    </message>
    <message>
        <source>{total} tools ({succeeded} ok, {failed} failed) in {time:.1f}s</source>
        <translation>{total} Werkzeuge ({succeeded} ok, {failed} fehlgeschlagen) in {time:.1f}s</translation>
    </message>
    <message>
        <source>{total} tools in {time:.1f}s</source>
        <translation>{total} Werkzeuge in {time:.1f}s</translation>
    </message>
</context>

<context>
    <name>RestoreBackupCommand</name>
    <message>
        <source>Restore from Backup...</source>
        <translation>Aus Sicherung wiederherstellen...</translation>
    </message>
    <message>
        <source>Open a copy of an automatic snapshot taken before the AI ran code</source>
        <translation>Eine Kopie eines automatischen Schnappschusses öffnen, der vor dem Ausführen von KI-Code erstellt wurde</translation>
    </message>
</context>

<context>
    <name>ToggleKeepDockCommand</name>
    <message>
        <source>Keep Chat Panel Open</source>
        <translation>Chatfenster geöffnet lassen</translation>
    </message>
    <message>
        <source>Toggle whether the FreeCAD AI chat panel stays open when switching to other workbenches</source>
        <translation>Umschalten, ob das FreeCAD-KI-Chatfenster beim Wechsel zu anderen Arbeitsbereichen geöffnet bleibt</translation>
    </message>
</context>

<context>
    <name>ToggleMCPServerCommand</name>
    <message>
        <source>MCP Server</source>
        <translation>MCP-Server</translation>
    </message>
    <message>
        <source>Start or stop the MCP server, letting external clients such as Claude Code drive this FreeCAD session. The server has no authentication unless a bearer token is configured; set its address and token in AI Settings.</source>
        <translation>Den MCP-Server starten oder stoppen, über den externe Clients wie Claude Code diese FreeCAD-Sitzung steuern können. Ohne konfiguriertes Bearer-Token hat der Server keine Authentifizierung; Adresse und Token legen Sie in den KI-Einstellungen fest.</translation>
    </message>
    <message>
        <source>MCP server failed to start</source>
        <translation>MCP-Server konnte nicht gestartet werden</translation>
    </message>
    <message>
        <source>Could not start the MCP server on {address}.

{error}

Change the address in FreeCAD AI → AI Settings → MCP Servers.</source>
        <translation>Der MCP-Server konnte auf {address} nicht gestartet werden.

{error}

Ändern Sie die Adresse unter FreeCAD KI → KI-Einstellungen → MCP-Server.</translation>
    </message>
</context>

</TS>
