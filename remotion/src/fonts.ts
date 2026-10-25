import {loadFont} from '@remotion/fonts';
import {staticFile} from 'remotion';

// The media service copies the brand fonts to these fixed names in the public dir.
export const FONT_FAMILY = 'BrandSans';

loadFont({family: FONT_FAMILY, url: staticFile('fonts/regular.ttf'), weight: '400'});
loadFont({family: FONT_FAMILY, url: staticFile('fonts/bold.ttf'), weight: '700'});
