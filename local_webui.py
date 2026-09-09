"""Optional web demo of the shared engine. The desktop app does not use this service."""
from __future__ import annotations
import argparse
from uuid import uuid4
from desktop_app.engine import Engine
from desktop_app.paths import app_root, configure_worker_environment, load_settings, user_data_dir


def main():
    parser = argparse.ArgumentParser(description="CosyVoice 共享引擎网页演示")
    parser.add_argument("--model-dir", "--model_dir", default=load_settings().get("model_dir"))
    parser.add_argument("--port", type=int, default=50000)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    if not args.model_dir:
        parser.error("请用 --model-dir 指定模型，或先在桌面版设置中选择模型。")
    session = configure_worker_environment()
    import gradio as gr
    engine = Engine(args.model_dir, session, progress=lambda data: print(data["message"], flush=True))
    output = user_data_dir() / "web-demo-results"

    def generate(text, reference, transcript, speed, seed):
        try:
            result = engine.synthesize(text, reference, transcript,
                                       output / (uuid4().hex + ".wav"), speed=float(speed), seed=seed)
            return result["path"]
        except (ValueError, RuntimeError) as exc:
            raise gr.Error(str(exc)) from exc

    with gr.Blocks(title="CosyVoice 共享引擎演示") as demo:
        gr.Markdown("# CosyVoice 共享引擎演示\n完整音色库、工程和 PPT 功能请使用桌面程序。")
        text = gr.Textbox(label="朗读文字", lines=7)
        reference = gr.Audio(label="参考录音", type="filepath", value=str(app_root() / "asset/zero_shot_prompt.wav"))
        transcript = gr.Textbox(label="参考录音原文", value="希望你以后能够做的比我还好呦。")
        speed = gr.Slider(0.5, 2, value=1, step=0.05, label="语速")
        seed = gr.Number(value=0, precision=0, label="随机种子")
        button = gr.Button("生成语音", variant="primary")
        result = gr.Audio(label="生成结果", type="filepath")
        button.click(generate, [text, reference, transcript, speed, seed], result, concurrency_limit=1)
    try:
        demo.queue(default_concurrency_limit=1).launch(server_name="127.0.0.1", server_port=args.port,
                                                     inbrowser=args.open_browser, show_error=True)
    finally:
        engine.close()


if __name__ == "__main__":
    main()
