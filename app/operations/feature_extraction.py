import sys
import numpy as np
import torch
import pathlib
from dotenv import load_dotenv
load_dotenv()
sys.path.append("..")
from operations.clip import clip
#from operations.longclip.model import longclip
APP_DIR = pathlib.Path(__file__).parent.absolute()

# Next set the project root directory.
PROJECT_DIR = APP_DIR.parent

# Set the model directory & create if it doesn't exist
MODELS_DIR = PROJECT_DIR / "models"
MODELS_DIR.mkdir(exist_ok=True)

class multimodal_embedding_clip:
    def __init__(self):
        #self.model = CLIPModel.from_pretrained("openai/clip-vit-large-patch14")
        self.device = 'cuda' if torch.cuda.is_available() else 'cpu'
        #self.processor = CLIPProcessor.from_pretrained("openai/clip-vit-large-patch14")
        self.model, self.preprocess = clip.load("ViT-L/14@336px", download_root=MODELS_DIR, device=self.device) # clip model
        #self.model, self.process = longclip.load(f"{MODELS_DIR}/longclip-B.pt", device=self.device) # long clip model
        self.model.to(self.device).eval()
    def embedding(self, x, modal):
        if modal == 'image':
            #if model == 'longclip':
            #x = [self.longClipPreprocess(image) for image in x]
            #else:
            x = [self.preprocess(image) for image in x]
        input_tensor = self.pre_processing_input(x, modal)
        output = self.forward(input_tensor, modal)
        output /= output.norm(dim=-1, keepdim=True)
        return output
    @torch.no_grad()
    def forward(self, x, modal):
        """Run model on input x."""
        if modal == "image":
            #if model == 'longclip':
            #output = self.model.encode_image(x)
            #else:
            output = self.model.encode_image(x).float()
        elif modal == "text":
            #if model == 'longclip':
            output = self.model.encode_text(x)
            #else:
            #output = self.model.encode_text(x).float()
        return output
    def zero_shot_cls(self, images, texts): # texts here is description of category
        image_features = self.embedding(images, 'image') # use clip for now for classification
        text_features = self.embedding(texts, 'text') # get embedding for description of category
        text_probs = (100.0 * image_features @ text_features.T).softmax(dim=-1)
        y_preds = ([item.item() for item in text_probs.argmax(dim=-1).cpu()])
        return y_preds, image_features

    def pre_processing_input(self, input, modal):
        """Decodes image from base64 encoding and transforms it."""
        if modal == "image":
            # with this image model, both models can use the same function below
            input_tensor = torch.tensor(np.stack(input)).to(self.device)
        elif modal == "text":
            #if model == 'longclip':
            #input_tensor = longclip.tokenize(input).to(self.device)
            #else:
            input_tensor = clip.tokenize(input).to(self.device)# using clip model
        return input_tensor

AUS_MULTIMODAL_MATCHING_CLIP = multimodal_embedding_clip()

if __name__ == '__main__':
    print("OK")



