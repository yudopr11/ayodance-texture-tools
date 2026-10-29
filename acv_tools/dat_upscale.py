"""4x RGB DAT inference: CPU state dictionaries or DirectML ONNX; alpha stays outside."""

from functools import lru_cache
from pathlib import Path

TILE = 128
BORDER = 32


@lru_cache(maxsize=1)
def load_model(path):
    from spandrel import ImageModelDescriptor, ModelLoader

    if Path(path).suffix.lower() not in ('.pth', '.safetensors'):
        raise ValueError('Use a trusted DAT .pth or .safetensors state dictionary')
    model = ModelLoader().load_from_file(path)
    if (not isinstance(model, ImageModelDescriptor) or model.architecture.id != 'DAT'
            or model.scale != 4 or model.input_channels != 3 or model.output_channels != 3):
        raise ValueError('Expected a 4x RGB DAT model')
    return model.eval()


def tiled_rgb(image, infer):
    """Bound inference memory; trim contextual borders before stitching tiles."""
    from PIL import Image

    width, height = image.size
    result = Image.new('RGB', (width * 4, height * 4))
    for y in range(0, height, TILE):
        for x in range(0, width, TILE):
            right, bottom = min(width, x + TILE), min(height, y + TILE)
            left, top = max(0, x - BORDER), max(0, y - BORDER)
            box = (left, top, min(width, right + BORDER), min(height, bottom + BORDER))
            enlarged = infer(image.crop(box))
            if enlarged.size != ((box[2] - left) * 4, (box[3] - top) * 4):
                raise ValueError('DAT tile output is not 4x the input dimensions')
            result.paste(enlarged.crop(((x - left) * 4, (y - top) * 4,
                                        (right - left) * 4, (bottom - top) * 4)), (x * 4, y * 4))
    # shortcut: DAT uses global context; inspect seams before increasing tile size for a final run.
    return result


@lru_cache(maxsize=1)
def load_onnx(path):
    import json
    import tempfile

    import numpy as np
    import onnxruntime as ort

    if 'DmlExecutionProvider' not in ort.get_available_providers():
        raise ValueError('ONNX GPU inference requires onnxruntime-directml')
    options = ort.SessionOptions()
    options.enable_mem_pattern = False
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.intra_op_num_threads = 2
    # Provider registration alone does not prove that any computation uses the GPU.
    with tempfile.TemporaryDirectory(prefix='dat-dml-placement-') as temporary:
        options.enable_profiling = True
        options.profile_file_prefix = str(Path(temporary) / 'placement')
        session = ort.InferenceSession(str(path), sess_options=options,
                                       providers=[('DmlExecutionProvider', {'device_id': '0'})])
        session.disable_fallback()
        if session.get_providers()[0] != 'DmlExecutionProvider':
            raise ValueError('DirectML session fell back to CPU')
        inputs, outputs = session.get_inputs(), session.get_outputs()
        if (len(inputs) != 1 or len(outputs) != 1 or inputs[0].type != 'tensor(float)'
                or len(inputs[0].shape) != 4 or inputs[0].shape[1] != 3):
            raise ValueError('Expected a single-input FP32 NCHW RGB DAT ONNX model')
        session.run(None, {inputs[0].name: np.zeros((1, 3, 16, 16), dtype=np.float32)})
        try:
            events = json.loads(Path(session.end_profiling()).read_text())
        except (OSError, ValueError) as error:
            raise ValueError('Could not validate DirectML execution profile') from error
        if not any(event.get('args', {}).get('provider') == 'DmlExecutionProvider'
                   and event['args'].get('op_name') not in (None, 'Identity', 'MemcpyFromHost', 'MemcpyToHost')
                   for event in events):
            raise ValueError('ONNX session has no profiled DirectML computation')
    return session


def upscale_onnx_image(source, destination, model_path):
    import numpy as np
    from PIL import Image

    session = load_onnx(str(model_path))
    name = session.get_inputs()[0].name

    def infer(tile):
        array = np.asarray(tile, dtype=np.float32) / 255.0
        # Match Spandrel's minimum-16 reflection, then replication for tiny inputs.
        pad_h, pad_w = max(0, 16 - tile.height), max(0, 16 - tile.width)
        reflect_h, reflect_w = min(pad_h, tile.height - 1), min(pad_w, tile.width - 1)
        array = np.pad(array, ((0, reflect_h), (0, reflect_w), (0, 0)), mode='reflect')
        array = np.pad(array, ((0, pad_h - reflect_h), (0, pad_w - reflect_w), (0, 0)), mode='edge')
        tensor = np.ascontiguousarray(array.transpose(2, 0, 1)[None])
        output = session.run(None, {name: tensor})[0]
        if not isinstance(output, np.ndarray) or output.shape != (1, 3, array.shape[0] * 4, array.shape[1] * 4):
            raise ValueError('DAT ONNX output is not 4x RGB')
        if not np.isfinite(output).all():
            raise ValueError('DAT ONNX produced non-finite pixels')
        pixels = output[0, :, :tile.height * 4, :tile.width * 4].transpose(1, 2, 0)
        return Image.fromarray(np.rint(np.clip(pixels, 0, 1) * 255).astype(np.uint8))

    with Image.open(source) as original:
        tiled_rgb(original.convert('RGB'), infer).save(destination)


def upscale_image(source, destination, model_path):
    if Path(model_path).suffix.lower() == '.onnx':
        return upscale_onnx_image(source, destination, model_path)
    import numpy as np
    import torch
    from PIL import Image

    model = load_model(str(model_path))

    def infer(tile):
        array = np.asarray(tile, dtype=np.float32) / 255.0
        tensor = torch.from_numpy(array).permute(2, 0, 1).unsqueeze(0).contiguous()
        with torch.inference_mode():
            output = model(tensor)
        if not torch.isfinite(output).all():
            raise ValueError('DAT produced non-finite pixels')
        array = output.squeeze(0).permute(1, 2, 0).clamp(0, 1).numpy()
        return Image.fromarray(np.rint(array * 255).astype(np.uint8))

    with Image.open(source) as original:
        tiled_rgb(original.convert('RGB'), infer).save(destination)
