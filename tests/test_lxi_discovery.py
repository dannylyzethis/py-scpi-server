import pytest

from scpi_emulator.emulator import SCPIInstrument
from scpi_emulator.hislip_transport import HiSLIPServer
from scpi_emulator.lxi_discovery import (
    HISLIP_SERVICE_TYPE,
    VXI11_SERVICE_TYPE,
    LXIDiscoveryAdvertiser,
    build_lxi_service_records,
)


def test_lxi_records_share_identity_and_use_configured_ports() -> None:
    instrument = SCPIInstrument("Discovery Test", "serial-1")

    records = build_lxi_service_records(
        instrument,
        host="127.0.0.1",
        hostname="scpi-test",
        service_name="Virtual Bench Instrument",
        hislip_port=4881,
        vxi11_port=1234,
    )

    assert [record.service_type for record in records] == [
        HISLIP_SERVICE_TYPE,
        VXI11_SERVICE_TYPE,
    ]
    assert {record.name.split(".", 1)[0] for record in records} == {
        "Virtual Bench Instrument"
    }
    assert {record.port for record in records} == {4881, 1234}
    assert all(record.server == "scpi-test.local." for record in records)
    assert list(records[0].properties)[:5] == [
        "txtvers",
        "Manufacturer",
        "Model",
        "SerialNumber",
        "FirmwareVersion",
    ]
    assert records[0].properties["VisaAddress"] == (
        "TCPIP::scpi-test.local.::hislip0,4881::INSTR"
    )


def test_lxi_record_validation() -> None:
    instrument = SCPIInstrument("Discovery Test", "serial-1")
    with pytest.raises(ValueError, match="at least one"):
        build_lxi_service_records(instrument, host="127.0.0.1")
    with pytest.raises(ValueError, match="between 1 and 65535"):
        build_lxi_service_records(instrument, host="127.0.0.1", hislip_port=0)


def test_pyvisa_discovers_and_opens_advertised_hislip(monkeypatch) -> None:
    zeroconf = pytest.importorskip("zeroconf")
    pyvisa = pytest.importorskip("pyvisa")
    from pyvisa_py import tcpip

    actual_zeroconf = zeroconf.Zeroconf
    monkeypatch.setattr(
        zeroconf,
        "Zeroconf",
        lambda *args, **kwargs: actual_zeroconf(interfaces=["127.0.0.1"]),
    )
    instrument = SCPIInstrument("Discovered HiSLIP", "discovered")
    server = HiSLIPServer(instrument, port=4880)
    if not server.start():
        pytest.skip("standard HiSLIP port 4880 is unavailable")
    advertiser = LXIDiscoveryAdvertiser(
        instrument,
        host="127.0.0.1",
        hostname="scpi-emulator-test",
        service_name="SCPI Emulator Test",
        hislip_port=4880,
        interfaces=["127.0.0.1"],
    )
    manager = pyvisa.ResourceManager("@py")
    resource = None
    try:
        advertiser.start()
        advertised = advertiser.records[0]
        assert advertiser.zeroconf.get_service_info(
            advertised.service_type, advertised.name, timeout=1000
        ).port == 4880
        resources = tcpip.TCPIPInstrHiSLIP.list_resources(wait_time=1.0)
        if not resources:
            pytest.skip("this host does not route multicast DNS over loopback")
        assert "TCPIP::127.0.0.1::hislip0,4880::INSTR" in resources
        resource = manager.open_resource("TCPIP::127.0.0.1::hislip0,4880::INSTR")
        resource.timeout = 2000
        assert resource.query("*IDN?").startswith(
            "SCPI_Emulator,Discovered HiSLIP,discovered,"
        )
    finally:
        if resource is not None:
            resource.close()
        manager.close()
        advertiser.stop()
        server.stop()
