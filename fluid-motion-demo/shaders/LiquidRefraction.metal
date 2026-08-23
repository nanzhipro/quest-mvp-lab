#include <metal_stdlib>
using namespace metal;

// SwiftUI::Layer 内联自系统 SwiftUI_Metal.h（自包含，规避 SwiftPM 编译 .metal 时缺 include 路径）
namespace SwiftUI {
struct Layer {
  metal::texture2d<half> tex;
  float2 info[5];
  half4 sample(float2 p) const {
    p = metal::fma(p.x, info[0], metal::fma(p.y, info[1], info[2]));
    p = metal::clamp(p, info[3], info[4]);
    return tex.sample(metal::sampler(metal::filter::linear), p);
  }
};
}

// 液体折射：采样相邻像素，按「环形衰减波 × 距原点距离」计算偏移，产生真·液体波纹。
[[ stitchable ]] half4 liquidRefraction(float2 position, SwiftUI::Layer layer,
                                        float2 origin, float time) {
    float2 delta = position - origin;
    float d = length(delta);
    float wave = sin(d * 0.06 - time * 5.0) * exp(-d * 0.006);
    float2 dir = (d > 0.001) ? (delta / d) : float2(0.0);
    float2 offset = dir * wave * 16.0;
    return layer.sample(position + offset);
}
