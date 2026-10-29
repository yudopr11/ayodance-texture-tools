"""Optional 4x RGB DAT inference on CPU; preserve alpha outside the network."""

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


def upscale_image(source, destination, model_path):
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
