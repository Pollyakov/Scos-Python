"""
Quick test: checks if Basler camera is connected and readable.
Run this after setup to verify everything works.
"""

def check_pypylon():
    try:
        from pypylon import pylon
    except ImportError:
        print("ERROR: pypylon not installed. Run: pip install pypylon")
        return False

    factory = pylon.TlFactory.GetInstance()
    devices = factory.EnumerateDevices()

    if len(devices) == 0:
        print("No Basler cameras found.")
        print("  -> Check USB/GigE cable")
        print("  -> Make sure Pylon SDK is installed (baslerweb.com)")
        return False

    print(f"Found {len(devices)} camera(s):")
    for i, dev in enumerate(devices):
        print(f"  [{i}] {dev.GetModelName()}  SN:{dev.GetSerialNumber()}  Interface:{dev.GetDeviceClass()}")

    # Grab one frame
    print("\nGrabbing one test frame...")
    camera = pylon.InstantCamera(factory.CreateFirstDevice())
    camera.Open()
    camera.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)
    result = camera.RetrieveResult(5000, pylon.TimeoutHandling_ThrowException)
    if result.GrabSucceeded():
        img = result.Array
        print(f"  Frame grabbed OK: shape={img.shape}, dtype={img.dtype}, max={img.max()}")
    else:
        print(f"  Grab failed: {result.ErrorDescription}")
    result.Release()
    camera.StopGrabbing()
    camera.Close()
    return True


if __name__ == "__main__":
    check_pypylon()
