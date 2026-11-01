using System.Collections;
using UnityEngine;

public sealed class i_mage_not_aiCaptureFixtureMover : MonoBehaviour
{
    private Vector3 origin;

    private IEnumerator Start()
    {
        origin = transform.position;
        yield return new WaitForSeconds(0.25f);
        i_mage_not_aiRenderCapture.MarkEvent("attack-start");
    }

    private void Update()
    {
        transform.position = origin + Vector3.right * Mathf.PingPong(Time.time * 2f, 3f);
    }
}
