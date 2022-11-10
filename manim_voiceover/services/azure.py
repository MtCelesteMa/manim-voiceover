import os
import re
import json
from dotenv import load_dotenv

try:
    import azure.cognitiveservices.speech as speechsdk
except ImportError:
    raise Exception(
        'Missing packages. Run `pip install manim-voiceover "manim-voiceover[azure]"` to use AzureService.'
    )

from manim_voiceover.services.base import SpeechService

load_dotenv()


def serialize_word_boundary(wb):
    return {
        "audio_offset": wb["audio_offset"],
        "duration_milliseconds": int(wb["duration_milliseconds"].microseconds / 1000),
        "text_offset": wb["text_offset"],
        "word_length": wb["word_length"],
        "text": wb["text"],
        "boundary_type": wb["boundary_type"],
    }


class AzureService(SpeechService):
    """Speech service for Azure TTS API."""
    def __init__(
        self,
        voice: str = "en-US-AriaNeural",
        # style="newscast-casual",
        style: str = None,
        output_format: str = "Audio48Khz192KBitRateMonoMp3",
        prosody: dict = None,
        **kwargs,
    ):
        """
        Args:
            voice (str, optional): The voice to use. See the `API page <https://learn.microsoft.com/en-us/azure/cognitive-services/speech-service/language-support?tabs=stt-tts>`__ for all the available options. Defaults to "en-US-AriaNeural".
            style (str, optional): The style to use. See the `API page <https://learn.microsoft.com/en-us/azure/cognitive-services/speech-service/rest-text-to-speech?tabs=streaming#style>`__ to see how you can see available styles for a given voice. Defaults to None.
            output_format (str, optional): The output format to use. See the `API page <https://learn.microsoft.com/en-us/azure/cognitive-services/speech-service/rest-text-to-speech?tabs=streaming#audio-outputs>`__ for all the available options. Defaults to "Audio48Khz192KBitRateMonoMp3".
            prosody (dict, optional): Global prosody settings to use. See the `API page <https://learn.microsoft.com/en-us/azure/cognitive-services/speech-service/speech-synthesis-markup#adjust-prosody>`__ for all the available options. Defaults to None.
        """
        self.voice = voice
        self.style = style
        self.output_format = output_format
        self.prosody = prosody
        SpeechService.__init__(self, **kwargs)

    def generate_from_text(
        self, text: str, output_dir: str = None, path: str = None, **kwargs
    ) -> dict:
        ""
        inner = text
        # Remove bookmarks
        inner = re.sub("<bookmark\s*mark\s*=['\"]\w*[\"']\s*/>", "", inner)
        if output_dir is None:
            output_dir = self.output_dir

        # Apply prosody
        prosody = self.prosody
        if "prosody" in kwargs:
            prosody = kwargs["prosody"]

        if prosody is not None:
            prosody = kwargs["prosody"]
            if not isinstance(prosody, dict):
                raise ValueError(
                    "The prosody argument must be a dict that contains at least one of the following keys: 'pitch', 'contour', 'range', 'rate', 'volume'."
                )

            opening_tag = (
                "<prosody "
                + " ".join(
                    ['%s="%s"' % (key, str(val)) for key, val in prosody.items()]
                )
                + ">"
            )
            inner = opening_tag + inner + "</prosody>"

        if self.style is not None:
            inner = r"""<mstts:express-as style="%s">
    %s
</mstts:express-as>""" % (
                self.style,
                inner,
            )

        ssml = r"""<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis"
    xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="en-US">
    <voice name="%s">
        %s
    </voice>
</speak>
        """ % (
            self.voice,
            inner,
        )

        data = {"input_text": text, "ssml": ssml, "config": self.__dict__}
        data_hash = self.get_data_hash(data)

        # Get the file extension from output_format
        if self.output_format[-3:] == "Mp3":
            file_extension = ".mp3"
        else:
            raise Exception("Unrecognized output format")

        if path is None:
            audio_path = os.path.join(output_dir, data_hash + ".mp3")
            json_path = os.path.join(output_dir, data_hash + ".json")

            if os.path.exists(json_path):
                return json.loads(open(json_path, "r").read())
        else:
            audio_path = path
            json_path = os.path.splitext(path)[0] + ".json"

        try:
            azure_subscription_key = os.environ["AZURE_SUBSCRIPTION_KEY"]
            azure_service_region = os.environ["AZURE_SERVICE_REGION"]
        except KeyError:
            raise Exception(
                "Microsoft Azure's text-to-speech API needs account credentials to connect. You can create an account for free and (as of writing this) get a free quota of TTS minutes. Check out the documentation for instructions."
            )

        speech_config = speechsdk.SpeechConfig(
            subscription=azure_subscription_key,
            region=azure_service_region,
        )
        speech_config.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat[self.output_format]
        )
        audio_config = speechsdk.audio.AudioOutputConfig(filename=audio_path)

        speech_service = speechsdk.SpeechSynthesizer(
            speech_config=speech_config, audio_config=audio_config
        )
        word_boundaries = []
        # speech_synthesizer.bookmark_reached.connect(lambda evt: print(
        #     "Bookmark reached: {}, audio offset: {}ms, bookmark text: {}.".format(evt, evt.audio_offset, evt.text)))

        def process_event(evt):
            # print(f'{type(evt)=}')
            result = {label[1:]: val for label, val in evt.__dict__.items()}
            result["boundary_type"] = result["boundary_type"].name
            result["text_offset"] = result["text_offset"] - 222  # TODO: make more clear
            return result

        speech_service.synthesis_word_boundary.connect(
            lambda evt: word_boundaries.append(process_event(evt))
        )

        speech_synthesis_result = speech_service.speak_ssml_async(ssml).get()

        json_dict = {
            "input_text": text,
            "ssml": ssml,
            "word_boundaries": [serialize_word_boundary(wb) for wb in word_boundaries],
            "original_audio": audio_path,
            "json_path": json_path,
        }

        # open(json_path, "w").write(json.dumps(json_dict, indent=2))
        if (
            speech_synthesis_result.reason
            == speechsdk.ResultReason.SynthesizingAudioCompleted
        ):
            pass
        elif speech_synthesis_result.reason == speechsdk.ResultReason.Canceled:
            cancellation_details = speech_synthesis_result.cancellation_details
            print("Speech synthesis canceled: {}".format(cancellation_details.reason))
            if cancellation_details.reason == speechsdk.CancellationReason.Error:
                if cancellation_details.error_details:
                    print(
                        "Error details: {}".format(cancellation_details.error_details)
                    )
            raise Exception("Speech synthesis failed")

        return json_dict