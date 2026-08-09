/* eslint-disable @next/next/no-img-element */
import { ImageResponse } from 'next/og';
import getImageSize from 'buffer-image-size';
import mime from 'mime';
import { existsSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { APP_CONFIG_DEFAULTS } from '@/app-config';
import { getAppConfig } from '@/lib/utils';

type Dimensions = {
  width: number;
  height: number;
};

type ImageData = {
  base64: string;
  dimensions: Dimensions;
};

// Image metadata
export const alt = 'Careva Health Helpline';
export const size = {
  width: 1200,
  height: 628,
};

function isRemoteFile(uri: string) {
  return uri.startsWith('http');
}

function resolveLocalPath(filePath: string) {
  const clean = filePath.replace(/^\/+/, '').replace(/^public\//, '');
  return join(process.cwd(), 'public', clean);
}

// LOCAL FILES MUST BE IN PUBLIC FOLDER
async function loadFileData(filePath: string): Promise<ArrayBuffer> {
  if (isRemoteFile(filePath)) {
    const response = await fetch(filePath);
    if (!response.ok) {
      throw new Error(`Failed to fetch ${filePath} - ${response.status} ${response.statusText}`);
    }
    return await response.arrayBuffer();
  }

  const localPath = resolveLocalPath(filePath);
  if (existsSync(localPath)) {
    const buffer = await readFile(localPath);
    return buffer.buffer.slice(
      buffer.byteOffset,
      buffer.byteOffset + buffer.byteLength
    ) as ArrayBuffer;
  }

  if (process.env.VERCEL_URL) {
    const publicFilePath = filePath.replace(/^\/+/, '').replace(/^public\//, '');
    const fontUrl = `https://${process.env.VERCEL_URL}/${publicFilePath}`;
    const response = await fetch(fontUrl);
    if (response.ok) {
      return await response.arrayBuffer();
    }
  }

  throw new Error(`File not found: ${filePath}`);
}

async function getImageData(uri: string, fallbackUri?: string): Promise<ImageData> {
  try {
    const fileData = await loadFileData(uri);
    const buffer = Buffer.from(fileData);
    const mimeType = mime.getType(uri) || 'image/png';

    return {
      base64: `data:${mimeType};base64,${buffer.toString('base64')}`,
      dimensions: getImageSize(buffer),
    };
  } catch (e) {
    if (fallbackUri) {
      return getImageData(fallbackUri);
    }
    throw e;
  }
}

function scaleImageSize(size: { width: number; height: number }, desiredHeight: number) {
  const scale = desiredHeight / (size.height || 1);
  return {
    width: (size.width || 1) * scale,
    height: desiredHeight,
  };
}

function cleanPageTitle(appName: string) {
  if (appName === APP_CONFIG_DEFAULTS.pageTitle) {
    return 'Voice agent';
  }

  return appName;
}

export const contentType = 'image/png';

// Image generation
export default async function Image() {
  const appConfig = await getAppConfig();

  const pageTitle = cleanPageTitle(appConfig.pageTitle);
  const logoUri = appConfig.logoDark || appConfig.logo || '/careva.png';

  // Load fonts - use file system in dev, fetch in production
  let commitMonoData: ArrayBuffer | undefined;
  let everettLightData: ArrayBuffer | undefined;

  try {
    commitMonoData = await loadFileData('commit-mono-400-regular.woff');
    everettLightData = await loadFileData('everett-light.woff');
  } catch (e) {
    console.error('Failed to load fonts:', e);
  }

  // bg
  const { base64: bgSrcBase64 } = await getImageData('opengraph-image-bg.png');

  // logo
  const { base64: logoSrcBase64, dimensions: logoDimensions } = await getImageData(logoUri);
  const logoSize = scaleImageSize(logoDimensions, 80);

  return new ImageResponse(
    (
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: size.width,
          height: size.height,
          backgroundImage: `url(${bgSrcBase64})`,
          backgroundSize: '100% 100%',
          backgroundPosition: 'center',
          backgroundRepeat: 'no-repeat',
        }}
      >
        <div
          style={{
            position: 'absolute',
            top: 180,
            left: 560,
            display: 'flex',
            alignItems: 'center',
            gap: 10,
          }}
        >
          {/* eslint-disable-next-line jsx-a11y/alt-text */}
          <img src={logoSrcBase64} width={logoSize.width} height={logoSize.height} />
        </div>
        <div
          style={{
            position: 'absolute',
            bottom: 100,
            left: 40,
            width: '450px',
            display: 'flex',
            flexDirection: 'column',
            gap: 16,
          }}
        >
          <div
            style={{
              backgroundColor: '#0D9488',
              padding: '4px 12px',
              borderRadius: 6,
              width: 80,
              fontSize: 12,
              fontFamily: 'CommitMono',
              fontWeight: 700,
              color: '#FFFFFF',
              letterSpacing: 0.8,
            }}
          >
            CAREVA
          </div>
          <div
            style={{
              fontSize: 44,
              fontWeight: 600,
              fontFamily: 'Everett',
              color: 'white',
              lineHeight: 1.1,
            }}
          >
            {pageTitle}
          </div>
        </div>
      </div>
    ),
    {
      ...size,
      fonts: [
        ...(commitMonoData
          ? [
              {
                name: 'CommitMono',
                data: commitMonoData,
                style: 'normal' as const,
                weight: 400 as const,
              },
            ]
          : []),
        ...(everettLightData
          ? [
              {
                name: 'Everett',
                data: everettLightData,
                style: 'normal' as const,
                weight: 300 as const,
              },
            ]
          : []),
      ],
    }
  );
}
