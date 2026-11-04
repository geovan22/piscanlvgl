#!/usr/bin/env python3
"""bt_pair_agent.py - Hace la Pi DESCUBRIBLE + EMPAREJABLE y auto-confirma el
emparejamiento entrante (agente org.bluez.Agent1 via D-Bus). Sirve para
emparejar un TELEFONO con la Pi (el telefono no se puede emparejar desde la Pi
porque anuncia con MAC random no-conectable; se empareja al reves).

Uso: bt_pair_agent.py [segundos] [hciN]   (default 150 hci0)
Corre como el usuario (geo22 habla con org.bluez por el bus del sistema).
SOLO lab / dispositivos propios o con autorizacion. Imprime eventos a stdout.
"""
import sys
import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib

AGENT_PATH = "/piscan/agent"
BLUEZ = "org.bluez"


class Agent(dbus.service.Object):
    """Agente que ACEPTA todo: just-works, numeric-comparison (auto-yes),
    passkey/pin por defecto. Para emparejar sin interaccion en la Pi."""

    @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
    def Release(self):
        pass

    @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
    def AuthorizeService(self, device, uuid):
        return  # autoriza cualquier servicio

    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="s")
    def RequestPinCode(self, device):
        return "0000"

    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="u")
    def RequestPasskey(self, device):
        return dbus.UInt32(0)

    @dbus.service.method("org.bluez.Agent1", in_signature="ou", out_signature="")
    def RequestConfirmation(self, device, passkey):
        print("AUTO-CONFIRM passkey=%06u dev=%s" % (int(passkey), device), flush=True)
        return  # sin excepcion => confirmado

    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="")
    def RequestAuthorization(self, device):
        return

    @dbus.service.method("org.bluez.Agent1", in_signature="ouq", out_signature="")
    def DisplayPasskey(self, device, passkey, entered):
        print("DisplayPasskey %06u" % int(passkey), flush=True)

    @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
    def DisplayPinCode(self, device, pincode):
        pass

    @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
    def Cancel(self):
        print("Cancel", flush=True)


def main():
    secs = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 150
    hci = sys.argv[2] if len(sys.argv) > 2 else "hci0"

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()

    apath = "/org/bluez/" + hci
    props = dbus.Interface(bus.get_object(BLUEZ, apath),
                           "org.freedesktop.DBus.Properties")
    for key, val in (("Powered", dbus.Boolean(True)),
                     ("Alias", dbus.String("PiScan")),
                     ("Pairable", dbus.Boolean(True)),
                     ("DiscoverableTimeout", dbus.UInt32(0)),
                     ("Discoverable", dbus.Boolean(True))):
        try:
            props.Set("org.bluez.Adapter1", key, val)
        except Exception as e:
            print("set %s err: %s" % (key, e), flush=True)

    Agent(bus, AGENT_PATH)
    mgr = dbus.Interface(bus.get_object(BLUEZ, "/org/bluez"),
                         "org.bluez.AgentManager1")
    mgr.RegisterAgent(AGENT_PATH, "KeyboardDisplay")
    mgr.RequestDefaultAgent(AGENT_PATH)
    print("PiScan agent LISTO: discoverable+pairable+auto-confirm %ds en %s"
          % (secs, hci), flush=True)

    loop = GLib.MainLoop()
    GLib.timeout_add_seconds(secs, loop.quit)
    try:
        loop.run()
    finally:
        try:
            props.Set("org.bluez.Adapter1", "Discoverable", dbus.Boolean(False))
        except Exception:
            pass
        print("agent fin", flush=True)


if __name__ == "__main__":
    main()
