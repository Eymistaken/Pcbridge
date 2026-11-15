//! One monitor's picture through DXGI Desktop Duplication.
//!
//! Each request opens a duplication of the monitor's output and takes its
//! first frame: that frame is the desktop as it is after the request, which
//! is what `freshness: "after_request"` asks for, and it avoids the stale
//! state of a long-lived duplication, which hands out no frame while nothing
//! on screen changes (measured in W0). The frame is copied to a staging
//! texture, read as BGRA row by row (the row pitch is not the width) and
//! turned into the shared `RgbaFrame`. The pointer is not drawn: duplication
//! leaves it out, and the answer says `include_pointer: false`.

use std::time::{Duration, Instant};

use pcbridge_core::{FrameId, FrameSpec, MAX_PIXELS, PixelFormat, RgbaFrame};
use windows::Win32::Foundation::HMODULE;
use windows::Win32::Graphics::Direct3D::D3D_DRIVER_TYPE_UNKNOWN;
use windows::Win32::Graphics::Direct3D11::{
    D3D11_CPU_ACCESS_READ, D3D11_CREATE_DEVICE_BGRA_SUPPORT, D3D11_MAP_READ,
    D3D11_MAPPED_SUBRESOURCE, D3D11_SDK_VERSION, D3D11_TEXTURE2D_DESC, D3D11_USAGE_STAGING,
    D3D11CreateDevice, ID3D11Device, ID3D11DeviceContext, ID3D11Texture2D,
};
use windows::Win32::Graphics::Dxgi::{
    CreateDXGIFactory1, DXGI_ERROR_WAIT_TIMEOUT, DXGI_OUTDUPL_FRAME_INFO, IDXGIAdapter1,
    IDXGIFactory1, IDXGIOutput, IDXGIOutput1, IDXGIResource,
};
use windows::core::Interface;

pub const BACKEND: &str = "windows.dxgi.duplication";

#[derive(Debug)]
pub struct Captured {
    pub frame: RgbaFrame,
    pub waited: Duration,
}

fn device_name(output: &IDXGIOutput) -> Option<String> {
    // SAFETY: GetDesc fills a structure the call returns by value.
    let desc = unsafe { output.GetDesc() }.ok()?;
    let end = desc
        .DeviceName
        .iter()
        .position(|&c| c == 0)
        .unwrap_or(desc.DeviceName.len());
    Some(String::from_utf16_lossy(&desc.DeviceName[..end]))
}

/// The adapter and output that show `device` (e.g. `\\.\DISPLAY1`).
fn find_output(device: &str) -> Result<(IDXGIAdapter1, IDXGIOutput), String> {
    // SAFETY: creates a factory object owned by the returned interface.
    let factory: IDXGIFactory1 =
        unsafe { CreateDXGIFactory1() }.map_err(|error| format!("CreateDXGIFactory1: {error}"))?;
    for adapter_index in 0.. {
        // SAFETY: enumeration ends with an error, which ends the loop.
        let Ok(adapter) = (unsafe { factory.EnumAdapters1(adapter_index) }) else {
            break;
        };
        for output_index in 0.. {
            // SAFETY: as above, per adapter.
            let Ok(output) = (unsafe { adapter.EnumOutputs(output_index) }) else {
                break;
            };
            if device_name(&output).as_deref() == Some(device) {
                return Ok((adapter, output));
            }
        }
    }
    Err(format!("no DXGI output shows {device}"))
}

/// The current picture of the monitor `device`, waiting at most `timeout`.
pub fn capture(device: &str, timeout: Duration) -> Result<Captured, String> {
    let started = Instant::now();
    let (adapter, output) = find_output(device)?;
    let mut device3d: Option<ID3D11Device> = None;
    let mut context: Option<ID3D11DeviceContext> = None;
    // SAFETY: out-parameters this frame owns; the adapter outlives the call.
    unsafe {
        D3D11CreateDevice(
            &adapter,
            D3D_DRIVER_TYPE_UNKNOWN,
            HMODULE::default(),
            D3D11_CREATE_DEVICE_BGRA_SUPPORT,
            None,
            D3D11_SDK_VERSION,
            Some(&mut device3d),
            None,
            Some(&mut context),
        )
    }
    .map_err(|error| format!("D3D11CreateDevice: {error}"))?;
    let device3d = device3d.ok_or("D3D11CreateDevice returned no device")?;
    let context = context.ok_or("D3D11CreateDevice returned no context")?;
    let output1: IDXGIOutput1 = output
        .cast()
        .map_err(|error| format!("IDXGIOutput1: {error}"))?;
    // SAFETY: duplicates the output for this device; released when dropped.
    let duplication = unsafe { output1.DuplicateOutput(&device3d) }
        .map_err(|error| format!("DuplicateOutput: {error}"))?;

    let mut info = DXGI_OUTDUPL_FRAME_INFO::default();
    let mut resource: Option<IDXGIResource> = None;
    let millis = u32::try_from(timeout.as_millis()).unwrap_or(u32::MAX);
    // SAFETY: two out-parameters this frame owns.
    match unsafe { duplication.AcquireNextFrame(millis, &mut info, &mut resource) } {
        Ok(()) => {}
        Err(error) if error.code() == DXGI_ERROR_WAIT_TIMEOUT => {
            return Err(format!("no frame within {} ms", timeout.as_millis()));
        }
        Err(error) => return Err(format!("AcquireNextFrame: {error}")),
    }
    let waited = started.elapsed();
    let result = read_frame(&device3d, &context, resource.as_ref());
    // SAFETY: a frame was acquired above and is released exactly once.
    let _ = unsafe { duplication.ReleaseFrame() };
    result.map(|frame| Captured { frame, waited })
}

fn read_frame(
    device: &ID3D11Device,
    context: &ID3D11DeviceContext,
    resource: Option<&IDXGIResource>,
) -> Result<RgbaFrame, String> {
    let texture: ID3D11Texture2D = resource
        .ok_or("the duplication returned no frame resource")?
        .cast()
        .map_err(|error| format!("ID3D11Texture2D: {error}"))?;
    let mut desc = D3D11_TEXTURE2D_DESC::default();
    // SAFETY: fills a structure this frame owns.
    unsafe { texture.GetDesc(&mut desc) };
    if u64::from(desc.Width) * u64::from(desc.Height) > MAX_PIXELS {
        return Err(format!(
            "{}x{} is above the frame limit",
            desc.Width, desc.Height
        ));
    }
    let staging_desc = D3D11_TEXTURE2D_DESC {
        Usage: D3D11_USAGE_STAGING,
        BindFlags: 0,
        CPUAccessFlags: D3D11_CPU_ACCESS_READ.0 as u32,
        MiscFlags: 0,
        ..desc
    };
    let mut staging: Option<ID3D11Texture2D> = None;
    // SAFETY: creates a texture owned by `staging`.
    unsafe { device.CreateTexture2D(&staging_desc, None, Some(&mut staging)) }
        .map_err(|error| format!("CreateTexture2D: {error}"))?;
    let staging = staging.ok_or("CreateTexture2D returned no texture")?;
    // SAFETY: both textures have the same description apart from usage.
    unsafe { context.CopyResource(&staging, &texture) };
    let mut mapped = D3D11_MAPPED_SUBRESOURCE::default();
    // SAFETY: maps subresource 0 for reading; unmapped below on every path.
    unsafe { context.Map(&staging, 0, D3D11_MAP_READ, 0, Some(&mut mapped)) }
        .map_err(|error| format!("Map: {error}"))?;
    let stride = mapped.RowPitch;
    let length = stride as usize * desc.Height as usize;
    // SAFETY: the mapping is RowPitch * Height bytes, valid until Unmap.
    let bytes = unsafe { std::slice::from_raw_parts(mapped.pData.cast::<u8>(), length) }.to_vec();
    // SAFETY: matches the Map above.
    unsafe { context.Unmap(&staging, 0) };
    let spec = FrameSpec {
        format: PixelFormat::Bgrx,
        width: desc.Width,
        height: desc.Height,
        stride,
        offset: 0,
        size: u32::try_from(length).map_err(|_| "frame too large".to_owned())?,
    };
    RgbaFrame::from_buffer(&spec, &bytes, FrameId::default()).map_err(|error| error.to_string())
}
