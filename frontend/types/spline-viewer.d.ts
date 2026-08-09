import * as React from 'react';

type SplineViewerAttributes = React.DetailedHTMLProps<
  React.HTMLAttributes<HTMLElement> & {
    url?: string;
    'events-target'?: string;
    loading?: 'auto' | 'lazy' | 'eager';
    'loading-anim-type'?: 'spinner' | 'none';
    style?: React.CSSProperties;
    className?: string;
    [key: string]: any;
  },
  HTMLElement
>;

declare module 'react' {
  namespace JSX {
    interface IntrinsicElements {
      'spline-viewer': SplineViewerAttributes;
    }
  }
}

declare global {
  namespace JSX {
    interface IntrinsicElements {
      'spline-viewer': SplineViewerAttributes;
    }
  }
  namespace React {
    namespace JSX {
      interface IntrinsicElements {
        'spline-viewer': SplineViewerAttributes;
      }
    }
  }
}
