using UnityEngine;

/// <summary>
/// Hook for Sony's mocopi sensor data reader.
/// Download mocopi-sensor-data-reader from the mocopi developer portal and import it
/// under phone/Assets/Sony. Do not commit that package.
/// In the SDK sample callback, forward each reading:
///   PhoneApp.Instance.PushFromSdk(sensorId, rotation, acceleration, battery01);
/// Rotation is the sensor attitude. Acceleration is in meters per second squared.
/// Battery is 0 to 1, or -1 when the SDK does not report it.
/// </summary>
public static class SonySensorReader
{
    public static void Forward(string sensorId, Quaternion rotation, Vector3 acceleration, float battery01)
    {
        if (PhoneApp.Instance == null || string.IsNullOrEmpty(sensorId))
            return;
        PhoneApp.Instance.PushFromSdk(sensorId, rotation, acceleration, battery01);
    }
}
