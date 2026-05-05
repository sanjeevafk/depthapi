import os
import re
import glob

def clean_text(text):
    # Remove footnote markers like [1], [2], etc.
    text = re.sub(r'\[\d+\]', '', text)
    
    # Remove Figure captions since images are missing
    text = re.sub(r'^Figure \d+\.\d+:.*$', '', text, flags=re.MULTILINE)
    
    # Fix markdown code blocks with inline Listing captions
    # e.g., ```Listing 4.1: Description -> ```\n**Listing 4.1: Description**
    text = re.sub(r'^```Listing (\d+\.\d+:.*)$', r'```\n**Listing \1**', text, flags=re.MULTILINE)
    
    # Reduce multiple blank lines to a single blank line
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    return text.strip()

def main():
    input_dir = '/home/sanjeev/Downloads/depthapi/dlwp_pages'
    output_dir = '/home/sanjeev/Downloads/depthapi/dlwp_pages_cleaned'
    
    os.makedirs(output_dir, exist_ok=True)
    
    files = glob.glob(os.path.join(input_dir, '*.txt'))
    
    for file_path in files:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        cleaned_content = clean_text(content)
        
        base_name = os.path.basename(file_path)
        output_path = os.path.join(output_dir, base_name)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(cleaned_content)
            
        print(f"Cleaned {base_name}")

if __name__ == '__main__':
    main()
